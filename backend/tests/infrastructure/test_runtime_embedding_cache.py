"""运行期只查缓存；主动准备仍可下载，冷缓存失败不阻断材料词法检索。"""

import json
import os
import socket
import subprocess
import sys
import textwrap

import pytest

from pmstudio.bootstrap.container import UuidIdGenerator
from pmstudio.infrastructure.postgres.embedding import FastEmbedEmbedder
from pmstudio.infrastructure.postgres.materials import SqliteMaterialStore
from tests.conftest import TEST_OWNER
from tests.support.builders import NOW, PROJECT, make_material_packet
from tests.support.postgres import open_direct


@pytest.mark.parametrize("lazy_load", [True, False])
@pytest.mark.parametrize(
    ("options", "expected_local_files_only"),
    [({}, False), ({"local_files_only": False}, False), ({"local_files_only": True}, True)],
    ids=["preparation-default", "preparation-explicit", "runtime-read-only"],
)
def test_cache_policy_reaches_real_fastembed_acquisition(
    monkeypatch, tmp_path, lazy_load, options, expected_local_files_only
):
    """保留真实 TextEmbedding 构造，只在获取模型前停住，避免测试下载大模型。"""
    from fastembed.text.onnx_embedding import OnnxTextEmbedding

    calls = []

    def stop_acquisition(cls, model, cache_dir, **kwargs):
        calls.append((model.model, kwargs["local_files_only"]))
        raise RuntimeError("model acquisition boundary")

    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path))
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setattr(OnnxTextEmbedding, "download_model", classmethod(stop_acquisition))

    if lazy_load:
        embedder = FastEmbedEmbedder(**options)
        assert calls == [], "懒加载不能在构造器里获取模型"
        with pytest.raises(RuntimeError, match="model acquisition boundary"):
            embedder.embed_many(["退货流程"])
    else:
        with pytest.raises(RuntimeError, match="model acquisition boundary"):
            FastEmbedEmbedder(lazy_load=False, **options)

    assert calls == [("BAAI/bge-small-zh-v1.5", expected_local_files_only)]


_RUNTIME_PROBE = textwrap.dedent(
    """
    import errno
    import json
    import os
    import socket
    import sys
    import time
    from importlib.metadata import version
    from pathlib import Path
    from urllib.parse import urlsplit

    from fastembed import TextEmbedding
    from fastembed.common.model_management import ModelManagement
    from pmstudio.bootstrap.container import build_container
    from pmstudio.bootstrap.settings import AppSettings
    from tests.conftest import TEST_OWNER
    from tests.support.builders import PROJECT

    hf_port = urlsplit(os.environ["HF_ENDPOINT"]).port
    with socket.socket() as probe:
        hf_errno = probe.connect_ex(("127.0.0.1", hf_port))
    assert hf_errno == errno.ECONNREFUSED, hf_errno
    cache_dir = Path(os.environ["FASTEMBED_CACHE_PATH"])
    assert not cache_dir.exists() or not any(cache_dir.iterdir())

    container = build_container(AppSettings(
        database_url=os.environ["PMSTUDIO_DATABASE_URL"],
        model="openai/gpt-4o-mini", api_key="sk-test", api_base=None,
    ))
    services = container.app.state.services
    retriever = services.runner._start_round._retriever
    embedder = retriever._embedder
    singleton = (
        services.upload_material._vectors._embedder is embedder
        and services.runner._run_seat_round._preparer._retriever is retriever
        and services.runner._integrate_round._preparer._retriever is retriever
    )
    assert singleton, "生产向量索引、建轮与每步上下文必须共用同一个 embedder"

    sleep_calls, connections, constructor_modes, acquisition_modes = [], [], [], []
    acquisition_cache_dirs = []

    def observe_call(frame, event, function):
        if event == "c_call" and function is time.sleep:
            sleep_calls.append(frame.f_locals.get("sleep"))
            print("SLEEP", sleep_calls[-1], flush=True)
        elif event == "call":
            if frame.f_code is TextEmbedding.__init__.__code__:
                constructor_modes.append(frame.f_locals["kwargs"].get("local_files_only", False))
            elif frame.f_code is ModelManagement.download_model.__func__.__code__:
                acquisition_modes.append(frame.f_locals["kwargs"].get("local_files_only", False))
                acquisition_cache_dirs.append(frame.f_locals["cache_dir"])

    def observe_network(event, args):
        if event == "socket.connect":
            connections.append(str(args[1]))

    sys.addaudithook(observe_network)
    sys.setprofile(observe_call)
    started = time.monotonic()
    try:
        embedder.embed_many(["退货流程"])
    except ValueError as error:
        cache_error = str(error)
    else:
        raise AssertionError("临时空缓存不应成功载入模型")
    embedding_seconds = time.monotonic() - started

    started = time.monotonic()
    hits = retriever.search(project_id=PROJECT, query="退货流程", owner=TEST_OWNER)
    retrieval_seconds = time.monotonic() - started
    sys.setprofile(None)
    print(json.dumps({
        "fastembed_version": version("fastembed"),
        "hf_connection_refused": hf_errno == errno.ECONNREFUSED,
        "singleton": singleton,
        "constructor_modes": constructor_modes,
        "acquisition_modes": acquisition_modes,
        "acquisition_cache_dirs": acquisition_cache_dirs,
        "sleep_calls": sleep_calls,
        "connections": connections,
        "cache_error": cache_error,
        "embedding_seconds": embedding_seconds,
        "retrieval_seconds": retrieval_seconds,
        "hit_ids": [str(hit.chunk_id) for hit in hits],
    }), flush=True)
    """
)


def test_production_runtime_cold_cache_fails_fast_and_retrieval_degrades(tmp_path):
    """独立进程、真实依赖、不可达 HF；不替换下载/退避/词法检索逻辑。"""
    with open_direct() as connection:
        packet = SqliteMaterialStore(connection, ids=UuidIdGenerator()).put(
            project_id=PROJECT,
            parsed=make_material_packet("退货流程：提交申请", title="售后手册").parsed,
            sha256="runtime-cache-fixture",
            content=b"raw",
            now=NOW,
            owner=TEST_OWNER,
        )

    env = dict(os.environ)
    for name, directory in (
        ("FASTEMBED_CACHE_PATH", "fastembed"),
        ("HF_HOME", "hf-home"),
        ("HF_HUB_CACHE", "hf-hub"),
        ("HUGGINGFACE_HUB_CACHE", "hf-hub"),
        ("XDG_CACHE_HOME", "xdg"),
    ):
        env[name] = str(tmp_path / directory)
    env.update(HF_HUB_OFFLINE="0", TRANSFORMERS_OFFLINE="0", NO_PROXY="*", no_proxy="*")
    for name in (
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"
    ):
        env.pop(name, None)

    # 占住但不 listen 的本地端口：子进程能实证 ECONNREFUSED，也不会撞上其他服务。
    with socket.socket() as unavailable_hf:
        unavailable_hf.bind(("127.0.0.1", 0))
        env["HF_ENDPOINT"] = f"http://127.0.0.1:{unavailable_hf.getsockname()[1]}"
        try:
            completed = subprocess.run(
                [sys.executable, "-c", _RUNTIME_PROBE],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            pytest.fail(
                f"运行期冷缓存超过 20 秒总上限：stdout={error.stdout!r}; stderr={error.stderr!r}"
            )

    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    result = json.loads(completed.stdout)
    print(f"runtime-cache-probe: {completed.stdout.strip()}")
    assert result["hf_connection_refused"] is True
    assert result["singleton"] is True
    assert result["constructor_modes"] == [True, True]
    assert result["acquisition_modes"] == [True, True]
    assert result["acquisition_cache_dirs"] == [env["FASTEMBED_CACHE_PATH"]] * 2
    assert result["sleep_calls"] == []
    assert result["connections"] == []
    assert result["cache_error"] == "Could not load model BAAI/bge-small-zh-v1.5 from any source."
    assert result["embedding_seconds"] < 3
    assert result["retrieval_seconds"] < 3
    assert result["hit_ids"] == [packet.parsed.chunks[0].chunk_id]
    assert "向量检索失败，退回纯词法" in completed.stderr
    assert not list((tmp_path / "fastembed").rglob("*")), "只读缓存不能落下模型/下载产物"

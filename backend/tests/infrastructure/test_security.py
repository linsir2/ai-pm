from pmstudio.infrastructure.security import hash_password, verify_password


def test_round_trip():
    hashed = hash_password("s3cret-密码")
    assert hashed != "s3cret-密码"
    assert hashed.startswith("$argon2id$")
    assert verify_password(hashed, "s3cret-密码") is True


def test_wrong_password_is_false_not_raise():
    hashed = hash_password("s3cret-密码")
    assert verify_password(hashed, "错的") is False


def test_same_password_hashes_differ():
    assert hash_password("x") != hash_password("x")

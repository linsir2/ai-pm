/* 首页左导航（文档库与模板库共用一份）。
 *
 * **为什么抽出来**：原来这三个锚点长在 `ProjectList` 里，于是模板库那一屏
 * （同一个顶层视图家族、视觉语言相同）**左导航整个消失**——只剩顶栏一个"模板"，
 * 两个页面看起来像两个产品。抽一份，两屏都用它。
 *
 * **两种形态，不两套文案**：文档库里"新建项目 / 最近项目"是**页内锚点**（那一屏
 * 上下都有内容，点了要滚过去）；模板库里那一屏没有这两个区块，锚点无处可指，
 * 于是退化成"回到项目库"的入口——文案与顺序不变，只是动作从"滚动"变成"换视图"。
 * `aria-current` 仍只给真正所在的那一项（`ProjectList.nav.test.tsx` 钉着这条）。 */

export interface HomeNavProps {
  /** 当前所在：项目库里的两个区块，或模板库。 */
  active: "new" | "recent" | "templates";
  /** 模板库那种形态：点前两项回项目库，而不是页内滚动。 */
  plain?: boolean;
  onNavigate?: (target: "new" | "recent") => void;
  onTemplates?: () => void;
}

export function HomeNav({ active, plain = false, onNavigate, onTemplates }: HomeNavProps) {
  const cls = (id: HomeNavProps["active"]) =>
    id === active ? "home__navlink home__navlink--active" : "home__navlink";

  const item = (id: "new" | "recent", label: string) => {
    if (plain) {
      return (
        <button
          type="button"
          className={cls(id)}
          aria-current={active === id ? "location" : undefined}
          onClick={() => onNavigate?.(id)}
          key={id}
        >
          {label}
        </button>
      );
    }
    return (
      <a
        className={cls(id)}
        href={`#home-${id}`}
        aria-current={active === id ? "location" : undefined}
        key={id}
      >
        {label}
      </a>
    );
  };

  return (
    <nav className="home__nav" aria-label="项目页导航">
      {item("new", "新建项目")}
      {item("recent", "最近项目")}
      <button type="button" className={cls("templates")} onClick={onTemplates}>
        模板库
      </button>
    </nav>
  );
}
from __future__ import annotations

import importlib
import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_deps_ensured = False


def _rmtree_chmod_retry(func: object, path: str, _exc_info: object) -> None:
    """Windows 上刪除唯讀檔時 rmtree 常失敗，先改寫入再重試。"""
    try:
        os.chmod(path, stat.S_IWRITE)
    except OSError:
        pass
    func(path)


def ensure_dependencies() -> None:
    """缺少 lxml / Pillow 時以目前解譯器自動 pip install，再繼續轉檔。"""
    global _deps_ensured
    if _deps_ensured:
        return

    required: list[tuple[str, str]] = [
        ("lxml", "lxml"),
        ("PIL", "Pillow"),
        ("numpy", "numpy"),
    ]
    missing = [pip for mod, pip in required if importlib.util.find_spec(mod) is None]
    if missing:
        print(f"缺少套件，正在安裝：{', '.join(missing)}")
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *missing],
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        importlib.invalidate_caches()

    _deps_ensured = True


@dataclass(frozen=True)
class Item:
    id: str
    x: int
    y: int
    w: int
    h: int


def _to_int(v: str | None, *, default: int = 0) -> int:
    if v is None:
        return default
    m = re.match(r"^\s*([+-]?\d+(?:\.\d+)?)", str(v))
    if not m:
        return default
    return int(round(float(m.group(1))))


def _parse_rect_path(d: str | None) -> tuple[int, int, int, int] | None:
    """
    嘗試解析「矩形路徑」的 path d，回傳 (x, y, w, h)。

    支援常見形式（Illustrator/PS 匯出常見）：
      Mx,y hW vH h-W v-H ...
    例如：M181.12,330h56.01v46.99h-56v-46.99h0Z
    """
    if not d:
        return None
    s = d.strip()
    # 保守解析：只支援「矩形」常見起手式：
    # - M x,y  h w  v h   （相對）
    # - M x,y  H x1 V y1  （絕對）
    # - 混用（例如 h + V）
    # 也支援另一種常見順序：M x,y v h H x ...（先垂直再水平）
    m = re.match(
        r"^\s*M\s*([+-]?\d+(?:\.\d+)?)\s*(?:,|\s)\s*([+-]?\d+(?:\.\d+)?)\s*"
        r"(?:"
        r"([hH])\s*([+-]?\d+(?:\.\d+)?)\s*([vV])\s*([+-]?\d+(?:\.\d+)?)"
        r"|"
        r"([vV])\s*([+-]?\d+(?:\.\d+)?)\s*([hH])\s*([+-]?\d+(?:\.\d+)?)"
        r")",
        s,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    x0 = float(m.group(1))
    y0 = float(m.group(2))
    if m.group(3):  # h then v
        h_cmd = m.group(3)
        h_val = float(m.group(4))
        v_cmd = m.group(5)
        v_val = float(m.group(6))
    else:  # v then h
        v_cmd = m.group(7)
        v_val = float(m.group(8))
        h_cmd = m.group(9)
        h_val = float(m.group(10))

    x1 = h_val if h_cmd == "H" else (x0 + h_val)
    y1 = v_val if v_cmd == "V" else (y0 + v_val)

    w = x1 - x0
    h = y1 - y0
    if w == 0 or h == 0:
        return None

    x = min(x0, x1)
    y = min(y0, y1)
    return (_to_int(str(x)), _to_int(str(y)), _to_int(str(abs(w))), _to_int(str(abs(h))))


def parse_svg_items(svg_path: Path) -> list[Item]:
    ensure_dependencies()
    from lxml import etree

    parser = etree.XMLParser(recover=True, huge_tree=True)
    root = etree.parse(str(svg_path), parser).getroot()

    img_dims: dict[str, tuple[int, int]] = {}
    for el in root.xpath("//*[local-name()='image']"):
        img_id = el.get("id")
        if not img_id:
            continue
        w = _to_int(el.get("width"))
        h = _to_int(el.get("height"))
        img_dims[img_id] = (w, h)

    items: list[Item] = []
    expected_use = 0
    expected_rect = 0
    expected_path = 0
    skipped_use_missing_href = 0
    skipped_use_missing_dims = 0
    skipped_rect_invalid = 0
    skipped_path_unparsed = 0
    skipped_path_invalid = 0

    for el in root.xpath("//*[local-name()='use']"):
        expected_use += 1
        use_id = el.get("id") or "item"
        href = el.get("href") or el.get("{http://www.w3.org/1999/xlink}href")
        if not href or not href.startswith("#"):
            skipped_use_missing_href += 1
            continue
        ref_id = href[1:]
        dims = img_dims.get(ref_id)
        if not dims:
            skipped_use_missing_dims += 1
            continue

        x = _to_int(el.get("x"))
        y = _to_int(el.get("y"))
        w, h = dims
        items.append(Item(id=use_id, x=x, y=y, w=w, h=h))

    # 很多 AI/PS 匯出會用 rect/path 標示切割框；為了「完全掃描所有物件」，
    # 這裡會同時收集 use/rect/path（不互斥），並動態檢查數量一致性。
    for el in root.xpath("//*[local-name()='rect']"):
        expected_rect += 1
        rid = el.get("id") or "rect"
        x = _to_int(el.get("x"))
        y = _to_int(el.get("y"))
        w = _to_int(el.get("width"))
        h = _to_int(el.get("height"))
        if w > 0 and h > 0:
            items.append(Item(id=rid, x=x, y=y, w=w, h=h))
        else:
            skipped_rect_invalid += 1

    for el in root.xpath("//*[local-name()='path']"):
        expected_path += 1
        pid = el.get("id") or "path"
        d = el.get("d")
        parsed = _parse_rect_path(d)
        if not parsed:
            skipped_path_unparsed += 1
            if d:
                preview = d.strip().replace("\n", " ")
                if len(preview) > 160:
                    preview = preview[:160] + "…"
                print(f"⚠ unparsed <path> id={pid}: d='{preview}'")
            continue
        x, y, w, h = parsed
        if w > 0 and h > 0:
            items.append(Item(id=pid, x=x, y=y, w=w, h=h))
        else:
            skipped_path_invalid += 1

    expected_total = expected_use + expected_rect + expected_path
    # 動態檢查：有宣告的框數 ≠ 可解析的框數時，印出原因（不影響輸出）
    if expected_total and len(items) != expected_total:
        print(
            "⚠ SVG 物件數量不一致："
            f"found={len(items)} expected={expected_total} "
            f"(use={expected_use}, rect={expected_rect}, path={expected_path}). "
            f"skipped(use_no_href={skipped_use_missing_href}, "
            f"use_no_dims={skipped_use_missing_dims}, "
            f"rect_invalid={skipped_rect_invalid}, "
            f"path_unparsed={skipped_path_unparsed}, "
            f"path_invalid={skipped_path_invalid})"
        )

    return items


def safe_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]+', "_", s)
    s = re.sub(r"\s+", "_", s).strip("_")
    return s or "item"


def _auto_items_from_png(img: "Image.Image", *, min_area: int = 64) -> list[Item]:
    """
    當 SVG 沒有提供切割框時，用 PNG 自動找可拆的區塊。
    以 alpha>0 的連通區塊 (8-connectivity) 當作一個 item。
    """
    ensure_dependencies()
    import numpy as np

    a = np.array(img.getchannel("A"))
    mask = a > 0
    if not mask.any():
        return []

    h, w = mask.shape
    visited = np.zeros((h, w), dtype=np.bool_)

    items: list[Item] = []
    nbrs = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

    ys, xs = np.nonzero(mask)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if visited[sy, sx]:
            continue

        stack = [(sy, sx)]
        visited[sy, sx] = True
        minx = maxx = sx
        miny = maxy = sy
        area = 0

        while stack:
            y, x = stack.pop()
            area += 1
            if x < minx:
                minx = x
            if x > maxx:
                maxx = x
            if y < miny:
                miny = y
            if y > maxy:
                maxy = y

            for dy, dx in nbrs:
                ny = y + dy
                nx = x + dx
                if ny < 0 or ny >= h or nx < 0 or nx >= w:
                    continue
                if visited[ny, nx] or not mask[ny, nx]:
                    continue
                visited[ny, nx] = True
                stack.append((ny, nx))

        if area < min_area:
            continue

        x0, y0 = int(minx), int(miny)
        x1, y1 = int(maxx) + 1, int(maxy) + 1
        items.append(Item(id=f"auto_{len(items)+1}", x=x0, y=y0, w=x1 - x0, h=y1 - y0))

    items.sort(key=lambda it: it.w * it.h, reverse=True)
    return items


def _fmt_coord(v: float) -> str:
    # Cocos 位置可用浮點；檔名避免多餘的 .0
    if float(v).is_integer():
        return str(int(v))
    return str(v).rstrip("0").rstrip(".")


def extract_one(png_path: Path, svg_path: Path, output_dir: Path) -> int:
    ensure_dependencies()
    from PIL import Image

    items = parse_svg_items(svg_path)
    output_dir.mkdir(parents=True, exist_ok=True)

    base = png_path.stem
    print(f"▶ {png_path.name} + {svg_path.name}")
    img = Image.open(png_path).convert("RGBA")
    W, H = img.size

    if not items:
        items = _auto_items_from_png(img)
        print(f"⚠ SVG 沒有切割框，改用 PNG 自動偵測：{len(items)} item(s)")
        if not items:
            return 0

    outputed = 0
    for it in items:
        x0, y0 = it.x, it.y
        x1, y1 = it.x + it.w, it.y + it.h

        cx0, cy0 = max(0, x0), max(0, y0)
        cx1, cy1 = min(W, x1), min(H, y1)
        if cx1 <= cx0 or cy1 <= cy0:
            continue

        crop = img.crop((cx0, cy0, cx1, cy1))
        # 裁切框中心點（以大圖左上為原點）
        center_x = x0 + (it.w / 2)
        center_y = y0 + (it.h / 2)
        # 轉成 Cocos 直接可用：以大圖中心為 (0,0)，x 右正、y 上正
        cocos_x = center_x - (W / 2)
        cocos_y = (H / 2) - center_y
        out_name = f"differ_{base}_{_fmt_coord(cocos_x)}_{_fmt_coord(cocos_y)}.png"
        out_path = output_dir / safe_name(out_name)
        crop.save(out_path)
        print(f"    → {out_path.name}")
        outputed += 1

    return outputed


def run_batch(script_dir: Path) -> None:
    ensure_dependencies()

    input_dir = script_dir / "input"
    output_dir = script_dir / "output"

    if not input_dir.is_dir():
        print(
            f"Skipped: 找不到輸入資料夾 {input_dir.resolve()} — "
            "請在腳本同層建立 input，並將成對的 png / svg 放入其中。"
        )
        return

    if output_dir.exists():
        try:
            shutil.rmtree(output_dir, onerror=_rmtree_chmod_retry)
        except OSError as e:
            print(
                "無法清空 output 資料夾（可能仍有檔案被占用：預覽窗、小畫家、防毒掃描等）。\n"
                f"路徑：{output_dir.resolve()}\n"
                f"錯誤：{e}",
                file=sys.stderr,
            )
            raise SystemExit(1) from e
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"已清空輸出資料夾：{output_dir.resolve()}")

    total_outputed = 0
    processed = 0
    skipped = 0

    for png_path in sorted(input_dir.glob("*.png")):
        # 避免把輸出檔再拿來切一次（若曾複製回 input）
        if png_path.name.startswith("item_"):
            continue

        svg_path = input_dir / f"{png_path.stem}.svg"
        if not svg_path.exists():
            skipped += 1
            continue

        n = extract_one(png_path, svg_path, output_dir)
        processed += 1
        total_outputed += n

    print(
        f"Done. Processed {processed} png(s), skipped {skipped} (no matching svg), "
        f"outputed {total_outputed} file(s) to: {output_dir.resolve()}"
    )


if __name__ == "__main__":
    import argparse

    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--dir",
        default=str(Path(__file__).resolve().parent),
        help="腳本根目錄（預設為本檔案同層）；png/svg 須放在其底下的 input/",
    )
    args = ap.parse_args()

    run_batch(Path(args.dir))

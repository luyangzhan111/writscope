"""Maintained, offline-first command line entry point."""

import argparse
from pathlib import Path
import sys


def main(argv=None):
    # Keep redirected Windows output readable without changing the user's locale.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="writscope", description="WritScope 约界 — 离线预置案例演示")
    parser.add_argument("--version", action="version", version="%(prog)s 0.1.0")
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo", help="生成三个预置案例的自包含页面，无模型调用")
    demo.add_argument("--output", type=Path, help="新输出目录；已有目录不会被覆盖")
    demo.add_argument("--open", action="store_true", help="生成后在浏览器打开本地文件")
    args = parser.parse_args(argv)
    try:
        from .demo import generate
        output = generate(args.output)
    except (OSError, ValueError) as error:
        print(f"WritScope demo: {error}", file=sys.stderr)
        return 1
    page = output / "index.html"
    print("离线演示已生成：3 个预置案例；模型调用 0；本次代码执行 0。")
    print(f"打开页面：{page}")
    print(f"浏览器地址：{page.as_uri()}")
    print(f"数据与文件哈希：{output / 'demo.json'}  {output / 'manifest.json'}")
    if args.open:
        import webbrowser
        try:
            opened = webbrowser.open(page.as_uri())
        except (OSError, webbrowser.Error):
            opened = False
        if not opened:
            print("未能自动打开浏览器，请手动打开上面的本地文件。", file=sys.stderr)
    return 0

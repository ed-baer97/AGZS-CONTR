"""Команды: serve, init, export."""

from __future__ import annotations

import argparse

from agzs.export import write_shift_exports
from agzs.server import open_app, serve


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Журнал АГЗС для колонки УЗГС-01")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_cmd = sub.add_parser("serve", help="открыть журнал в браузере")
    serve_cmd.add_argument("--db", default="data/agzs.sqlite", help="файл базы")
    serve_cmd.add_argument("--port", type=int, default=8765)
    serve_cmd.add_argument("--serial", default="", help="порт АСКА, например /dev/ttyUSB0")

    init_cmd = sub.add_parser("init", help="создать базу и учебную смену, если база пустая")
    init_cmd.add_argument("--db", default="data/agzs.sqlite", help="файл базы")

    export_cmd = sub.add_parser("export", help="записать журнал и продажи в файлы")
    export_cmd.add_argument("--db", default="data/agzs.sqlite", help="файл базы")
    export_cmd.add_argument("--out", default="exports")
    export_cmd.add_argument("--shift", type=int, default=14)

    args = parser.parse_args(argv)
    if args.command == "init":
        app = open_app(args.db)
        app.store.close()
        print(f"База: {args.db}")
        return
    if args.command == "export":
        app = open_app(args.db)
        try:
            written = write_shift_exports(app.store, args.out, args.shift)
        finally:
            app.store.close()
        for path in written:
            print(path)
        return
    app = open_app(args.db, serial=args.serial or None)
    server = serve(app, args.port)
    host, port = server.server_address[:2]
    print(f"Журнал: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()
        app.controller.link.close()
        app.store.close()

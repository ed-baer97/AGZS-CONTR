"""Запуск API. Адрес берётся из окружения, в код не вшивается."""

import os


def main() -> None:
    import uvicorn

    from agzs_server.api.app import create_app
    from agzs_server.db import default_database_url

    host = os.environ.get("AGZS_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("AGZS_BIND_PORT", "8000"))
    uvicorn.run(create_app(default_database_url()), host=host, port=port)


if __name__ == "__main__":
    main()

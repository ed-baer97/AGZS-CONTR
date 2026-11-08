# AGZS Control

Система автоматизации автомобильных газозаправочных станций: локальная касса на Windows и центральный сервер на Ubuntu.

Текущий этап — управление колонками и съём показаний на эмуляторе: статус, доза и суммарный счётчик. Оплата и фискальные чеки отложены. АСКА-02, Kaspi и Webkassa закрыты, пока нет устройства и контрактов. Деньги в уже записанных продажах хранятся целыми тенге.

## Документы

- [Архитектура](docs/ARCHITECTURE.md)
- [База данных](docs/DATABASE.md)
- [Жизненный цикл продажи](docs/SALES_FLOW.md)
- [Колонки, эмулятор и АСКА-02](docs/PUMP_PROTOCOL.md)
- [План разработки](docs/DEVELOPMENT_ROADMAP.md)
- [Риски и открытые вопросы](docs/RISKS.md)

## Локальный запуск каркаса

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

Переменные окружения перечислены в `.env.example`. Пустые значения не подставляют адреса и секреты. Файл `.env` не коммитится.

Промышленный профиль (`AGZS_ENV=production`) собирается только с драйвером `aska02`. Этот драйвер отклоняет обмен, а продажа не создаётся без утверждённой политики. Профиль `emulator` считает колонки в памяти.

```bash
AGZS_ENV=emulator AGZS_PUMP_DRIVER=emulator .venv/bin/agzs-station --ui
AGZS_ENV=production AGZS_PUMP_DRIVER=aska02 .venv/bin/agzs-station
```

Окно ставится отдельно: `pip install "agzs-station[ui]"`. Учебный пароль задаётся `AGZS_BOOTSTRAP_PASSWORD` либо один раз записывается в `data/bootstrap-password.txt`. Как провести смену — в [docs/CASHIER.md](docs/CASHIER.md).

Центр:

```bash
.venv/bin/agzs-server
```

`CENTRAL_DATABASE_URL`, `CENTRAL_BOOTSTRAP_USER` и `CENTRAL_BOOTSTRAP_PASSWORD` берутся из окружения. Состав Docker и резервная копия описаны в [docs/OPERATIONS.md](docs/OPERATIONS.md).

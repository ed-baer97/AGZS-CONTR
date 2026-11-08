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
- [Журнал УЗГС-01](docs/uzsg-01.md)

## Журнал УЗГС-01

Колонка УЗГС-01 (она же УЗСГ-01, «Технопроект») сама журнал не ведёт. В кадре Искра 1.72 она отдаёт цену, объём и код состояния. Программа `agzs` собирает из кадров журнал событий и выгрузку продаж. Что колонка может и чего не может — в [docs/uzsg-01.md](docs/uzsg-01.md) и на вкладке «Что отдаёт УЗГС-01».

Пока адаптер не подключён, в базе лежит смена 14 за 8 ноября 2026 (пояс Asia/Qyzylorda, UTC+5):

- 12 чеков, 320,200 л, 28 818,00 тенге
- файлы: `exports/zhurnal-smena-14.txt`, `exports/zhurnal-smena-14.csv`, `exports/prodazhi-smena-14.csv`, `exports/prodazhi-smena-14.json`

Сумма считается в тенге: кадр передаёт только число цены, валюту выбирает программа. Суммарник в выгрузке учётный. Штатный счётчик колонки смотрят на табло клавишами 9 0 2 и сверяют при закрытии смены.

Нужен Python 3.11+, без дополнительных пакетов.

```bash
python -m agzs serve
```

Журнал открывается на http://127.0.0.1:8765 и слушает только этот адрес.

```bash
python -m agzs export --shift 14 --out exports
python -m agzs serve --serial /dev/ttyUSB0
```

Живая колонка идёт через АСКА-01 или USB-блок, 9600 8N1. На колонке должен быть включён режим внешнего управления: в программировании горят только буквы Н и П. Без порта программа остаётся на имитаторе и пишет об этом в шапке.

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

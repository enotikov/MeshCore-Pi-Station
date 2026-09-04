# MeshCore Pi Station — руководство на русском

## Назначение

MeshCore Pi Station превращает Raspberry Pi в локальную станцию управления MeshCore Companion Radio, подключённым по USB. Интерфейс открывается в браузере компьютера, телефона или экрана самого Raspberry Pi. Облачный сервис для обмена сообщениями не требуется.

Приложение пока не прошивает Heltec и по умолчанию запускается с симулятором. Режим реального USB-модема включается отдельно после установки подходящей Companion Firmware на Heltec V4.

## Возможности версии 0.3.0

- русский и английский интерфейс с сохранением выбранного языка;
- личные и канальные сообщения, локальная история в SQLite;
- контакты, избранное, блокировка, заметки и QR-обмен;
- создание, изменение, удаление и экспорт каналов;
- мониторинг RSSI, SNR, noise floor, счётчиков пакетов и ошибок;
- подробная карточка каждого пакета и его metadata;
- географическая карта узлов с фиксированным масштабом и перемещением;
- онлайн-подложка OpenStreetMap либо полностью автономная MBTiles;
- отображение маршрутов и трёхсекундная анимация RX/TX пакетов;
- запрос телеметрии, поиск, установка и сброс пути, trace;
- настройки частоты, BW, SF, CR, координат и TX Power;
- выбор координат узла кликом на географической карте в настройках;
- ограничение мощности фактическим максимумом, сообщённым платой;
- расширенные настройки duty cycle, CAD, RX gain и flood hops;
- advert, WebSocket-обновления и системные уведомления браузера;
- AES-256-GCM backup, отдельный защищённый экспорт identity и CSV;
- PWA-оболочка, продолжающая открываться без интернета;
- необязательная HTTP Basic авторизация.

## Требования

- Raspberry Pi 3, 4 или 5;
- Raspberry Pi OS Bookworm, 32-bit или 64-bit;
- Python 3.11 или новее;
- сетевое подключение во время первой установки Python-зависимостей;
- свободный порт TCP 8080;
- для реального радио: Heltec V4 с USB Companion Firmware и USB-кабель с линиями данных.

## Установка готового `.deb`

```bash
sudo apt update
sudo apt install ./meshcore-pi-station_0.3.0_all.deb
sudo systemctl status meshcore-pi-station
```

Откройте `http://<IP-Raspberry-Pi>:8080`. Узнать IP можно командой `hostname -I`. После установки используется симулятор, поэтому интерфейс можно проверить до подключения и прошивки Heltec.

## Подключение USB Companion Radio

```bash
ls -l /dev/serial/by-id/ /dev/ttyACM* /dev/ttyUSB* 2>/dev/null
```

Откройте `/etc/default/meshcore-pi-station` и измените:

```text
MESHCORE_TRANSPORT=serial
MESHCORE_SERIAL_PORT=auto
MESHCORE_SERIAL_BAUD=115200
```

```bash
sudo systemctl restart meshcore-pi-station
sudo journalctl -u meshcore-pi-station -f
```

Служебный пользователь `meshcore` автоматически добавляется в группу `dialout`.

## Офлайн-карта

Поместите MBTiles, например, в `/var/lib/meshcore-pi-station/maps/region.mbtiles` и задайте:

```text
MESHCORE_MBTILES_PATH=/var/lib/meshcore-pi-station/maps/region.mbtiles
```

После перезапуска MBTiles получает приоритет над сетевой подложкой. Не скачивайте публичные тайлы OpenStreetMap массово — для автономной работы используйте заранее подготовленный MBTiles-файл.

## Основные параметры

| Переменная | По умолчанию | Назначение |
|---|---|---|
| `MESHCORE_HOST` | `0.0.0.0` | Адрес веб-сервера |
| `MESHCORE_PORT` | `8080` | HTTP-порт |
| `MESHCORE_DATA_DIR` | `/var/lib/meshcore-pi-station` | SQLite и данные |
| `MESHCORE_TRANSPORT` | `mock` | `mock` или `serial` |
| `MESHCORE_SERIAL_PORT` | `auto` | Автопоиск USB-порта или явный путь |
| `MESHCORE_SERIAL_BAUD` | `115200` | Скорость USB serial |
| `MESHCORE_RADIO_DEBUG` | `false` | Диагностический лог транспорта |
| `MESHCORE_WEB_PASSWORD` | пусто | Пароль пользователя `meshcore` |
| `MESHCORE_MBTILES_PATH` | пусто | Путь к локальной карте |
| `MESHCORE_TILE_URL` | OpenStreetMap | Сетевая подложка при отсутствии MBTiles |

После изменения конфигурации выполните `sudo systemctl restart meshcore-pi-station`.

## Безопасность

Приложение предназначено для доверенной локальной сети. Не публикуйте порт 8080 напрямую в интернет. Для авторизации задайте `MESHCORE_WEB_PASSWORD=длинный-уникальный-пароль`; пользователь — `meshcore`. Для удалённого доступа рекомендуется VPN или HTTPS reverse proxy. Identity содержит приватный ключ узла — храните экспорт отдельно.

## Обновление, удаление и диагностика

```bash
sudo apt install ./meshcore-pi-station_<новая-версия>_all.deb
sudo systemctl status meshcore-pi-station
sudo journalctl -u meshcore-pi-station -n 200 --no-pager
curl http://127.0.0.1:8080/api/status
sudo apt remove meshcore-pi-station
```

Данные остаются в `/var/lib/meshcore-pi-station`, чтобы исключить случайную потерю истории и ключей. Удаляйте каталог вручную только после backup.

## Разработка и сборка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
MESHCORE_TRANSPORT=mock meshcore-pi-station
pytest
python3 scripts/build_deb.py
```

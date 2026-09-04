# MeshCore Pi Station — руководство на русском

## Назначение

MeshCore Pi Station превращает Raspberry Pi в локальную станцию управления MeshCore Companion Radio, подключённым по USB или Bluetooth Low Energy. Интерфейс открывается в браузере компьютера, телефона или экрана самого Raspberry Pi. Облачный сервис для обмена сообщениями не требуется.

Приложение пока не прошивает Heltec и по умолчанию запускается с симулятором. Реальный USB- или BLE-модем включается отдельно после установки подходящей Companion Firmware на Heltec V4.

## Возможности версии 0.5.0

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
- подключение Companion Radio по USB serial или Bluetooth Low Energy;
- встроенный HTTPS и настраиваемая HTTP Basic авторизация.
- безопасная прошивка Heltec V4 через USB из веб-интерфейса с отображением прогресса.

## Требования

- Raspberry Pi 3, 4 или 5;
- Raspberry Pi OS Bookworm, 32-bit или 64-bit;
- Python 3.11 или новее;
- сетевое подключение во время первой установки Python-зависимостей;
- свободный порт TCP 8080;
- для реального радио: Heltec V4 с Companion Firmware, USB-кабель с линиями данных либо рабочий BLE.

## Установка готового `.deb`

```bash
sudo apt update
sudo apt install ./meshcore-pi-station_0.5.0_all.deb
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

## Прошивка Heltec V4 через веб-интерфейс

Функция по умолчанию отключена. Сначала включите HTTPS и вход по паролю, затем добавьте в `/etc/default/meshcore-pi-station`:

```text
MESHCORE_FIRMWARE_FLASH=true
MESHCORE_FIRMWARE_BAUD=460800
```

Перезапустите службу и откройте «Настройки → Прошивка Heltec V4». Подключите плату к Raspberry Pi по USB-кабелю с линиями данных, выберите файл и введите точное подтверждение `HELTEC V4`.

- Обычный `*.bin` — режим «Обновление», запись приложения по адресу `0x10000` без очистки пользовательских данных.
- `*merged.bin` — режим «Полная», очистка flash и запись полного образа с адреса `0x0`.

Станция принимает только файлы до 16 MiB с сигнатурой ESP image. Перед записью она освобождает serial-порт, запускает `esptool`, показывает прогресс и затем автоматически пытается снова подключиться к Companion. Не отключайте питание или USB во время операции. Всегда проверяйте, что образ собран именно для Heltec V4 / ESP32-S3; приложение не может определить разводку платы по содержимому файла.

## Подключение Bluetooth Companion Radio

Убедитесь, что Bluetooth работает и найдите устройство:

```bash
sudo systemctl enable --now bluetooth
bluetoothctl scan on
```

Затем задайте в `/etc/default/meshcore-pi-station`:

```text
MESHCORE_TRANSPORT=ble
MESHCORE_BLE_ADDRESS=auto
MESHCORE_BLE_PIN=
```

`auto` ищет устройство с именем MeshCore. Для однозначного выбора укажите MAC-адрес, например `AA:BB:CC:DD:EE:FF`. PIN нужен только если его требует Companion Firmware. После изменения перезапустите службу. Если Bluetooth-модуль Heltec аппаратно неисправен, используйте USB serial.

## HTTPS и вход по логину/паролю

Создайте самоподписанный сертификат для локальной сети:

```bash
sudo /usr/lib/meshcore-pi-station/scripts/generate_tls.sh
```

В `/etc/default/meshcore-pi-station` задайте:

```text
MESHCORE_WEB_USERNAME=operator
MESHCORE_WEB_PASSWORD=замените-на-длинный-уникальный-пароль
MESHCORE_TLS_CERT=/var/lib/meshcore-pi-station/tls/server.crt
MESHCORE_TLS_KEY=/var/lib/meshcore-pi-station/tls/server.key
```

После `sudo systemctl restart meshcore-pi-station` откройте `https://<IP-Raspberry-Pi>:8080`. Браузер предупредит о самоподписанном сертификате; для доверенного HTTPS укажите сертификат и ключ от вашего центра сертификации. Если пароль пуст, проверка входа отключена. Сертификат и ключ должны быть указаны вместе.

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
| `MESHCORE_PORT` | `8080` | HTTP- или HTTPS-порт |
| `MESHCORE_DATA_DIR` | `/var/lib/meshcore-pi-station` | SQLite и данные |
| `MESHCORE_TRANSPORT` | `mock` | `mock`, `serial` или `ble` |
| `MESHCORE_SERIAL_PORT` | `auto` | Автопоиск USB-порта или явный путь |
| `MESHCORE_SERIAL_BAUD` | `115200` | Скорость USB serial |
| `MESHCORE_BLE_ADDRESS` | `auto` | Автопоиск BLE или MAC-адрес Heltec |
| `MESHCORE_BLE_PIN` | пусто | Необязательный PIN BLE-сопряжения |
| `MESHCORE_RADIO_DEBUG` | `false` | Диагностический лог транспорта |
| `MESHCORE_WEB_USERNAME` | `meshcore` | Имя пользователя веб-интерфейса |
| `MESHCORE_WEB_PASSWORD` | пусто | Пароль; пустое значение отключает вход |
| `MESHCORE_TLS_CERT` | пусто | PEM-сертификат HTTPS |
| `MESHCORE_TLS_KEY` | пусто | Закрытый PEM-ключ HTTPS |
| `MESHCORE_TLS_KEY_PASSWORD` | пусто | Пароль зашифрованного TLS-ключа |
| `MESHCORE_FIRMWARE_FLASH` | `false` | Разрешить прошивку Heltec из веб-интерфейса |
| `MESHCORE_FIRMWARE_BAUD` | `460800` | Скорость записи через `esptool` |
| `MESHCORE_MBTILES_PATH` | пусто | Путь к локальной карте |
| `MESHCORE_TILE_URL` | OpenStreetMap | Сетевая подложка при отсутствии MBTiles |

После изменения конфигурации выполните `sudo systemctl restart meshcore-pi-station`.

## Безопасность

Не публикуйте приложение в интернет без HTTPS и пароля. Для локального самоподписанного сертификата используйте поставляемый генератор, а для публичного имени — доверенный сертификат. HTTP Basic передаёт учётные данные безопасно только внутри TLS. Identity содержит приватный ключ узла — храните экспорт отдельно.

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

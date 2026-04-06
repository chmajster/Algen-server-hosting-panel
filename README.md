# Hosting Panel

Produkcyjny szkielet panelu do zarządzania hostingiem przez WWW oparty o Flask, SQLAlchemy, MariaDB i Bootstrap 5.

## Moduły

- panel administratora i klienta
- uwierzytelnianie, role, sesje, CSRF i rate limiting logowania
- billing oparty o saldo klienta i cykle rozliczeń
- domeny, subdomeny, bazy danych, FTP, DNS, SSL, poczta, backupy
- webowy menedżer plików dla klienta z separacją per klient
- monitoring usług i metryki serwera
- bezpieczny helper do zarządzania `/etc/hosts` przez `sudo`
- wdrożenie przez `install.sh`, Gunicorn, systemd i opcjonalny nginx

## Szybki start developerski

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
flask --app wsgi:app db upgrade
flask --app wsgi:app seed-data
python app.py
```

## Wdrożenie

```bash
chmod +x install.sh
sudo ./install.sh
```

Installer tworzy i włącza usługę `systemd`, która autostartuje aplikację po restarcie serwera.

Najważniejsze polecenia:

```bash
sudo systemctl status hosting-panel.service
sudo systemctl restart hosting-panel.service
sudo systemctl enable hosting-panel.service
```

Jeśli chcesz odtworzyć sam serwis bez pełnej reinstalacji:

```bash
sudo APP_DIR=/opt/hosting-panel APP_USER=hosting-panel APP_GROUP=hosting-panel /opt/hosting-panel/scripts/install_app_service.sh
```

## Dane startowe

- administrator: `admin`
- hasło administratora: generowane przez installer albo `ChangeMe123!` w seedzie developerskim
- klient demo: `client1`
- hasło klienta demo: `Client123!`

## Harmonogram billingowy

Przykład dla `cron`:

```cron
*/15 * * * * /opt/hosting-panel/.venv/bin/python /opt/hosting-panel/scripts/run_billing_cycle.py >> /var/log/hosting-panel/billing-cron.log 2>&1
```

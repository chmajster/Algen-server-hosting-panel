# Hosting Panel

Produkcyjny szkielet panelu do zarządzania hostingiem przez WWW oparty o Flask, SQLAlchemy, MariaDB i Bootstrap 5.

Installer został przebudowany tak, aby używał systemowego Pythona z repozytorium APT Ubuntu zamiast kompilacji ze źródeł. Jeśli `python3.14` jest dostępny w oficjalnym repo danej wersji Ubuntu, zostanie użyty; w przeciwnym razie installer przejdzie na domyślne `python3` z tego samego repo.

## Moduły

- panel administratora i klienta
- uwierzytelnianie, role, sesje, CSRF i rate limiting logowania
- billing oparty o saldo klienta i cykle rozliczeń
- domeny, subdomeny, bazy danych, FTP, DNS, SSL, poczta, backupy
- webowy menedżer plików dla klienta z separacją per klient
- monitoring usług i metryki serwera
- bezpieczny helper do zarządzania `/etc/hosts` przez `sudo`
- generowanie i odnawianie certyfikatów SSL dla domen i subdomen
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

Przyklad z podaniem hasla startowego administratora:

```bash
sudo ./install.sh -p 'TwojeHaslo123!'
```

Installer ma kolorowy, etapowy output i na końcu pokazuje publiczny adres IP serwera oraz adres panelu na porcie `80`.
Przy ponownej instalacji, jeśli konto administratora już istnieje, installer nie zmienia jego hasła.
Logowanie ma osobny limit prób oparty o rzeczywisty adres IP klienta za nginx i zwraca własny widok `429`, zamiast surowej strony biblioteki.
Po instalacji installer wykonuje też informacyjny test usług i pokazuje, czy `hosting-panel`, `mariadb`, `nginx`, timer auto-update oraz sam panel HTTP odpowiadają poprawnie.

Installer instaluje jeden z wariantów:

- preferowany:
  - `python3.14`
  - `python3.14-venv`
  - `python3.14-dev`
- fallback z oficjalnego APT Ubuntu:
  - `python3`
  - `python3-venv`
  - `python3-dev`

Installer tworzy i włącza usługę `systemd`, która autostartuje aplikację po restarcie serwera.
Panel jest publikowany przez nginx na porcie `80`.

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

## Instalacja z GitHub Release

Możesz instalować aplikację bez ręcznego klonowania repozytorium. Bootstrap installer pobiera paczkę release z:

`https://github.com/chmajster/Algen-server-hosting-panel`

Instalacja najnowszego release:

```bash
curl -fsSL https://raw.githubusercontent.com/chmajster/Algen-server-hosting-panel/main/scripts/bootstrap_release_install.sh | sudo bash
```

Instalacja konkretnego taga release:

```bash
curl -fsSL https://raw.githubusercontent.com/chmajster/Algen-server-hosting-panel/main/scripts/bootstrap_release_install.sh | sudo RELEASE_TAG=v1.0.0 bash
```

Release asset, którego używa bootstrap:

`hosting-panel-release.tar.gz`

Do publikacji release po tagu `v*` służy workflow:

[.github/workflows/release.yml](C:/Users/Chris/Documents/GitHub/Algen-server-hosting-panel/.github/workflows/release.yml)

Paczka release jest budowana przez:

[scripts/build_release_bundle.sh](C:/Users/Chris/Documents/GitHub/Algen-server-hosting-panel/scripts/build_release_bundle.sh)

## Auto-update z GitHub

Projekt ma teraz wbudowany mechanizm auto-update dla repo:

`https://github.com/chmajster/Algen-server-hosting-panel`

Po instalacji, jeśli `AUTOUPDATE_ENABLED=true`, installer tworzy:

- usługę `hosting-panel-update.service`
- timer `hosting-panel-update.timer`
- skrypt aktualizujący `/usr/local/bin/hosting-panel-update`

Mechanizm:

- sprawdza najnowszy commit w repo GitHub
- pobiera nową wersję do katalogu tymczasowego
- synchronizuje kod do `/opt/hosting-panel`
- zachowuje `.env`, `.venv` i `storage/`
- instaluje zależności z `requirements.txt`
- uruchamia migracje bazy
- restartuje `hosting-panel.service`

## SSL dla domen i subdomen

Panel obsługuje teraz certyfikaty SSL zarówno dla domen głównych, jak i subdomen.

Wdrożenie instaluje:

- helper `/usr/local/bin/hosting-panel-ssl-helper`
- regułę sudo dla ograniczonego uruchamiania helpera
- pakiet `certbot`

Z poziomu panelu można:

- utworzyć rekord certyfikatu dla domeny albo subdomeny
- wygenerować certyfikat Let's Encrypt
- odnowić istniejący certyfikat
- zapisać ścieżki do certyfikatów manualnych

Konfiguracja:

```env
SSL_HELPER_PATH=/usr/local/bin/hosting-panel-ssl-helper
LETSENCRYPT_EMAIL=admin@example.com
```

Przydatne polecenia:

```bash
sudo systemctl status hosting-panel-update.timer
sudo systemctl start hosting-panel-update.service
sudo journalctl -u hosting-panel-update.service -n 100 --no-pager
```

Zmienne konfiguracyjne:

```env
AUTOUPDATE_ENABLED=true
AUTOUPDATE_REPO_URL=https://github.com/chmajster/Algen-server-hosting-panel
AUTOUPDATE_BRANCH=main
AUTOUPDATE_INTERVAL='*:0/15'
```

## Dane startowe

- administrator: `admin`
- hasło administratora: generowane przez installer przy pierwszym utworzeniu albo `ChangeMe123!` w seedzie developerskim
- klient demo: `client1`
- hasło klienta demo: `Client123!`

## Harmonogram billingowy

Przykład dla `cron`:

```cron
*/15 * * * * /opt/hosting-panel/.venv/bin/python /opt/hosting-panel/scripts/run_billing_cycle.py >> /var/log/hosting-panel/billing-cron.log 2>&1
```

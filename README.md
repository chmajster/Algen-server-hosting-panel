# Hosting Panel

Produkcyjny szkielet panelu do zarządzania hostingiem przez WWW oparty o Flask, SQLAlchemy, MariaDB i Bootstrap 5.

Installer został przebudowany tak, aby używał systemowego Pythona z repozytorium APT Ubuntu zamiast kompilacji ze źródeł. Jeśli `python3.14` jest dostępny w oficjalnym repo danej wersji Ubuntu, zostanie użyty; w przeciwnym razie installer przejdzie na domyślne `python3` z tego samego repo.

## Moduły

- panel administratora i klienta
- rola `operator` z dostepem do panelu administracyjnego (podobnie jak administrator)
- publiczna rejestracja klienta z wyborem planu hostingu
- uwierzytelnianie, role, sesje, CSRF i rate limiting logowania
- opcjonalne 2FA (Google Authenticator lub kod e-mail) na poziomie konta uzytkownika
- billing oparty o saldo klienta i cykle rozliczeń
- opcjonalne platnosci online (provider Stripe lub mock do testow)
- system ticketow klient <-> administrator/operator
- domeny, subdomeny, bazy danych, FTP, DNS, SSL, poczta, backupy
- instalacja i publikacja phpMyAdmin pod `/phpmyadmin/` (link z panelu admina i klienta)
- 1 kontener Docker z Apache per klient, z automatyczna synchronizacja VirtualHostow
- webowy menedżer plików dla klienta z separacją per klient
- monitoring usług i metryki serwera
- polityki retencji i privacy per tenant (z legal hold i cleanup run history)
- secrets vault z wersjonowaniem, szyfrowaniem at-rest i rotacja
- near-real-time event stream (admin + tenant) oraz eksport event/compliance
- compliance center i policy-as-code dla krytycznych akcji operacyjnych
- onboarding wizard klienta oraz panel DR readiness (RPO/RTO, region coverage)
- bezpieczny helper do zarządzania `/etc/hosts` przez `sudo`
- generowanie i odnawianie certyfikatów SSL dla domen i subdomen
- wdrożenie przez `install.sh`, Gunicorn, systemd i opcjonalny nginx

## Operacje governance (retencja / compliance / DR / vault)

Nowe moduly governance uruchamiane sa przez istniejace komendy CLI Flask.

Wymagane / zalecane zmienne `.env`:

```env
SECRETS_VAULT_KEY=change-this-vault-key
APPROVALS_ENABLED=true
APPROVALS_RISKY_ACTIONS=domains.delete,backups.restore
APPROVALS_REQUIRED_COUNTS=domains.delete=1,backups.restore=1
```

Przykladowe zadania harmonogramu (cron/systemd timer), uruchamiane z katalogu aplikacji i aktywnym `.venv`:

```bash
flask --app wsgi:app run-retention-cleanup --run-key "daily-$(date +%Y%m%d)"
flask --app wsgi:app run-secret-rotation-scan
flask --app wsgi:app run-compliance-checks
flask --app wsgi:app run-dr-checks
```

Opcjonalny, bezpieczny test failover (symulacja) dla konkretnego tenanta:

```bash
flask --app wsgi:app run-dr-failover-test --client-id 1
```

Uwagi operacyjne:

- cleanup retencji jest idempotentny po `run_key`;
- vault sekrety pozwalaja na jednorazowe ujawnienie plaintext per wersja sekretu;
- event stream redaguje wrazliwe pola (`password`, `token`, `secret`, itd.);
- compliance center ma checklist controls z ownerem, due date i tenant-scoped evidence links.

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

Jesli konto administratora juz istnieje, uruchomienie z `-p` wymusi aktualizacje jego hasla.

Installer ma kolorowy, etapowy output i na końcu pokazuje publiczny adres IP serwera oraz adres panelu na porcie `80`.
Przy ponownej instalacji, jeśli konto administratora już istnieje, installer nie zmienia jego hasła bez jawnego użycia argumentu `-p`.
Logowanie ma osobny limit prób oparty o rzeczywisty adres IP klienta za nginx i zwraca własny widok `429`, zamiast surowej strony biblioteki.
Po instalacji installer wykonuje też informacyjny test usług i pokazuje, czy `hosting-panel`, `mariadb`, `nginx`, `php-fpm`, timer auto-update, panel HTTP oraz `phpMyAdmin` odpowiadają poprawnie.

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
phpMyAdmin jest instalowany automatycznie i dostępny pod adresem `/phpmyadmin/`.

## Bazy danych klienta

Panel klienta pozwala teraz na:

- zakładanie kont użytkowników DB z poziomu panelu klienta,
- zarządzanie uprawnieniami użytkowników DB (np. `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `ALL`),
- zmianę hosta i statusu konta DB,
- reset hasła użytkownika DB.

Wymuszenie nazewnictwa kont DB:

- każdy użytkownik DB tworzony przez klienta dostaje prefiks loginu klienta,
- przykład: dla loginu `client1` konto DB zostanie zapisane jako `client1_nazwa`.

## Rejestracja z planem

Ekran logowania udostepnia rejestracje klienta (`/auth/register`).

Podczas rejestracji:

- klient wybiera aktywny plan hostingu,
- zakladane jest konto klienta i przypisywana usluga typu `hosting`,
- limity planu sa kopiowane do profilu klienta,
- limity CPU/RAM planu sa uzywane do limitow kontenera Docker Apache klienta.

Konfiguracja `.env`:

```env
SELF_REGISTRATION_ENABLED=true
REGISTRATION_AUTO_LOGIN=true
```

## Tickety (klient <-> administrator/operator)

Panel udostepnia prosty system ticketow:

- klient tworzy ticket i prowadzi korespondencje z poziomu `Panel klienta -> Tickety`,
- administrator i operator obsluguja wszystkie zgłoszenia w `Panel administracyjny -> Tickety`,
- staff moze odpowiadac, zmieniac status, priorytet i przypisywac zgłoszenie do operatora,
- klient moze zamknac ticket; nowa wiadomosc klienta ponownie otwiera zgłoszenie,
- opcjonalnie system wysyla powiadomienia e-mail: do staff po wiadomosci klienta oraz do klienta po odpowiedzi staff.

Konfiguracja `.env` dla powiadomien ticketow:

```env
TICKETS_EMAIL_NOTIFICATIONS_ENABLED=true
TICKETS_EMAIL_SUBJECT_NEW_CLIENT_TICKET='Nowy ticket klienta: {ticket}'
TICKETS_EMAIL_SUBJECT_CLIENT_REPLY='Nowa odpowiedz klienta: {ticket}'
TICKETS_EMAIL_SUBJECT_STAFF_REPLY='Nowa odpowiedz supportu: {ticket}'
```

## Apache per klient (Docker)

Panel obsluguje model:

- jeden klient = jeden kontener Docker z Apache,
- wszystkie domeny i subdomeny klienta sa mapowane jako VirtualHosty w tym kontenerze,
- limity CPU/RAM kontenera sa wyliczane z planu hostingowego klienta (`limits_json` planu),
- po kazdej zmianie domen (utworzenie, edycja, usuniecie, dodanie subdomeny) konfiguracja kontenera jest synchronizowana automatycznie.

Aby ustawic limity dla planu, w panelu administratora przy edycji planu uzupelnij pola:

- `CPU (vCPU)` np. `1`, `1.5`,
- `RAM (MB)` np. `1024`, `2048`.

Instalator:

- instaluje `docker.io`,
- uruchamia `docker.service`,
- dodaje uzytkownika uslugi panelu do grupy `docker`,
- wlacza funkcje kontenerow Apache per klient przez `.env`.

Zmienne konfiguracyjne:

```env
CLIENT_APACHE_ENABLED=true
CLIENT_APACHE_IMAGE=httpd:2.4
CLIENT_APACHE_BIND_ADDRESS=127.0.0.1
CLIENT_APACHE_HTTP_PORT_BASE=18000
CLIENT_APACHE_CONTAINER_PREFIX=hosting-panel-client-apache
CLIENT_APACHE_REMOVE_EMPTY=true
```

Port HTTP kontenera klienta jest wyliczany jako:

`CLIENT_APACHE_HTTP_PORT_BASE + client_id`

Najważniejsze polecenia:

```bash
sudo systemctl status hosting-panel.service
sudo systemctl restart hosting-panel.service
sudo systemctl enable hosting-panel.service
```

## Smoketest aplikacji

Mozesz uruchomic szybki smoketest na dwa sposoby:

- z konsoli (CLI Flask):

```bash
cd /opt/hosting-panel
. .venv/bin/activate
flask --app wsgi:app smoke-test
```

- z panelu administratora: menu `Smoketest` lub karta na dashboardzie admina.

- przez endpoint JSON do monitoringu zewnetrznego:

```bash
curl -H "X-Smoke-Test-Token: <SMOKE_TEST_API_TOKEN>" http://127.0.0.1/monitoring/smoke-test.json
```

Uwagi bezpieczenstwa:

- endpoint akceptuje token **wylacznie** z naglowka `X-Smoke-Test-Token` (bez query param),
- endpoint jest ograniczony allowlista IP (`SMOKE_TEST_API_ALLOWLIST`),
- endpoint ma osobny rate limit (`SMOKE_TEST_API_RATELIMIT`).

Smoketest sprawdza:

- polaczenie z baza danych,
- obecność kluczowych endpointow aplikacji,
- dostepnosc katalogow `STORAGE_ROOT`, `CLIENT_HOME_ROOT`, `BACKUP_ROOT`,
- runtime Docker, jesli wlaczone jest `CLIENT_APACHE_ENABLED=true`,
- zgodnosc kontenerow Apache per klient (obecnosc, status, mapowanie portow, `httpd -t`).

Kazde uruchomienie smoketestu zapisuje log JSON do:

`/var/log/hosting-panel/smoke-test.log`

Harmonogram automatyczny (systemd timer):

- usluga: `hosting-panel-smoke-test.service`,
- timer: `hosting-panel-smoke-test.timer`,
- domyslny interwal: `*:0/15`.

Przydatne polecenia:

```bash
sudo systemctl status hosting-panel-smoke-test.timer
sudo systemctl start hosting-panel-smoke-test.service
sudo journalctl -u hosting-panel-smoke-test.service -n 100 --no-pager
tail -n 50 /var/log/hosting-panel/smoke-test.log
```

Konfiguracja `.env`:

```env
SMOKE_TEST_LOG_FILE=/var/log/hosting-panel/smoke-test.log
SMOKE_TEST_API_TOKEN=change-this-smoke-token
SMOKE_TEST_API_ALLOWLIST=127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
SMOKE_TEST_API_RATELIMIT='5 per minute'
SMOKE_TEST_SCHEDULE_ENABLED=true
SMOKE_TEST_INTERVAL='*:0/15'
```

## Ograniczenie panelu admin

Panel administratora (`/admin/...`) moze byc ograniczony do sieci lokalnej/VPN.

Domyslnie funkcja jest wlaczona i korzysta z allowlisty prywatnych podsieci.

```env
ADMIN_LOCAL_ONLY=true
ADMIN_ALLOWED_NETWORKS=127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
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

## Opcjonalne 2FA i platnosci online

Nowe funkcje sa dostepne, ale domyslnie **wylaczone**:

- 2FA dla logowania uzytkownika (Google Authenticator albo kod e-mail),
- doladowanie salda klienta przez platnosc online.

Po wlaczeniu `TWO_FACTOR_AVAILABLE=true`, kazdy uzytkownik moze sam wlaczyc 2FA w panelu (menu `2FA`) i wybrac metode:

- Google Authenticator (TOTP),
- kod jednorazowy wysylany na e-mail.

Po wlaczeniu `ONLINE_PAYMENTS_ENABLED=true`, klient dostaje formularz doladowania salda na stronie `Billing`.

Konfiguracja `.env`:

```env
TWO_FACTOR_AVAILABLE=false
TWO_FACTOR_ISSUER='Hosting Panel'
TWO_FACTOR_LOGIN_RATELIMIT='10 per 10 minutes'
TWO_FACTOR_EMAIL_ENABLED=true
TWO_FACTOR_EMAIL_CODE_TTL_SECONDS=300
TWO_FACTOR_EMAIL_SUBJECT='Kod logowania 2FA'
MAIL_SERVER=
MAIL_PORT=587
MAIL_USERNAME=
MAIL_PASSWORD=
MAIL_USE_TLS=true
MAIL_USE_SSL=false
MAIL_FROM='no-reply@example.com'
TICKETS_EMAIL_NOTIFICATIONS_ENABLED=true
TICKETS_EMAIL_SUBJECT_NEW_CLIENT_TICKET='Nowy ticket klienta: {ticket}'
TICKETS_EMAIL_SUBJECT_CLIENT_REPLY='Nowa odpowiedz klienta: {ticket}'
TICKETS_EMAIL_SUBJECT_STAFF_REPLY='Nowa odpowiedz supportu: {ticket}'

ONLINE_PAYMENTS_ENABLED=false
ONLINE_PAYMENTS_PROVIDER=stripe
ONLINE_PAYMENTS_CURRENCY=PLN
ONLINE_PAYMENTS_MIN_AMOUNT=5.00
ONLINE_PAYMENTS_MAX_AMOUNT=50000.00
ONLINE_PAYMENTS_SUCCESS_URL=
ONLINE_PAYMENTS_CANCEL_URL=

STRIPE_SECRET_KEY=
STRIPE_PUBLISHABLE_KEY=
STRIPE_WEBHOOK_SECRET=
STRIPE_WEBHOOK_TOLERANCE_SECONDS=300
```

Uwagi:

- dla 2FA e-mail i powiadomien ticketow wymagane jest skonfigurowanie SMTP (`MAIL_SERVER`, `MAIL_PORT`, `MAIL_USERNAME`, `MAIL_PASSWORD`),
- provider `mock` pozwala przetestowac przeplyw checkout bez zewnetrznej bramki,
- webhook Stripe jest obslugiwany pod adresem `POST /webhooks/stripe`,
- przy Stripe rekomendowane jest ustawienie poprawnego HTTPS i `STRIPE_WEBHOOK_SECRET`.

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

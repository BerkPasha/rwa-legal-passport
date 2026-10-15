# RWA Legal Passport

Tokenize edilmiş gerçek dünya varlıkları (Real-World Assets) için kurumsal yasal şeffaflık API'si.

---

## Gereksinimler

- Python 3.11 veya üstü
- Windows 10/11

---

## Kurulum (Windows)

### 1. Projeyi klonla veya aç

```powershell
cd C:\Users\<kullanici-adin>\rwa-legal-passport
```

### 2. Sanal ortam oluştur

```powershell
python -m venv .venv
```

### 3. Sanal ortamı aktif et

```powershell
.venv\Scripts\activate
```

> Aktif olduğunda terminalde `(.venv)` görürsün.

### 4. Bağımlılıkları kur

```powershell
pip install -r requirements.txt
```

### 5. Ortam değişkenlerini kopyala

```powershell
copy .env.example .env
```

### 6. Kimlik bilgilerini ayarla

`.env` içindeki `REVIEWER_ISSUER_TOKEN`, `REVIEWER_LEGAL_TOKEN`, `INSTITUTIONAL_API_KEY_ALPHA` ve `INSTITUTIONAL_API_KEY_BETA` değerlerini doldur. Güçlü bir değer üretmek için:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Boş bırakılan token/anahtar devre dışıdır. Bu değerler yalnızca `.env` dosyasında durur ve Git'e eklenmez; arayüzde kenar çubuğundaki **Credentials** alanına girilir.

---

## Sunucuyu Çalıştır

```powershell
uvicorn app.main:app --reload
```

Sunucu `http://127.0.0.1:8000` adresinde başlar.

| URL | Açıklama |
|-----|----------|
| `http://127.0.0.1:8000/health` | Sağlık kontrolü |
| `http://127.0.0.1:8000/docs` | Swagger UI (interaktif API dökümantasyonu) |
| `http://127.0.0.1:8000/redoc` | ReDoc dökümantasyonu |

---

## Testleri Çalıştır

```powershell
pytest -v
```

---

## Proje Yapısı

```
rwa-legal-passport/
├── app/
│   ├── __init__.py
│   ├── main.py          # FastAPI uygulaması
│   ├── config.py        # Ayarlar (pydantic-settings)
│   └── routers/
│       ├── __init__.py
│       └── health.py    # /health endpoint'i
├── tests/
│   ├── __init__.py
│   └── test_health.py   # Sağlık testleri
├── .env                 # Ortam değişkenleri (git'e ekleme!)
├── .env.example         # Örnek ortam değişkenleri
├── pytest.ini           # Pytest yapılandırması
├── requirements.txt     # Python bağımlılıkları
└── README.md
```

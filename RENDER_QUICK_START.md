# ⚡ Quick Start: Render.com (5 minutos)

## 🎯 Passo a Passo Rápido

### 1️⃣ Criar Conta
- Acesse: https://render.com
- Login com **GitHub**

### 2️⃣ Criar PostgreSQL (opcional)
- **New +** → **PostgreSQL**
- **Region**: **US East (Ohio)** ⚠️
- **Plan**: Free
- **Copie a Internal Database URL**

### 3️⃣ Criar Web Service
- **New +** → **Web Service**
- Conecte repositório: `larian3/simlam-telegram-bot`
- **Region**: **US East (Ohio)** ⚠️ **CRÍTICO!**
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `python bot.py`
- **Plan**: Free

### 4️⃣ Variáveis de Ambiente
```
BOT_TOKEN=seu_token_aqui
DATABASE_URL=postgresql://...
SIMLAM_CONNECT_TIMEOUT=10
SIMLAM_READ_TIMEOUT=90
SIMLAM_PDF_READ_TIMEOUT=240
PORT=8080
```

### 5️⃣ Deploy
- Clique em **"Create Web Service"**
- Aguarde ~3-5 minutos
- ✅ Pronto!

---

## ⚠️ IMPORTANTE

**SEMPRE escolha região US (Ohio ou Oregon)** no Render para evitar bloqueio de IP!

---

## 📝 Checklist

- [ ] PostgreSQL criado (região US)
- [ ] Web Service criado (região US)
- [ ] Variáveis configuradas
- [ ] Deploy concluído
- [ ] Bot testado

---

**Dúvidas?** Veja `MIGRACAO_RENDER.md` para guia completo.





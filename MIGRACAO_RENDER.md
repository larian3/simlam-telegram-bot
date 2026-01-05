# 🚀 Guia de Migração: Koyeb → Render.com

## 📋 Pré-requisitos

- ✅ Conta no GitHub (seu repositório já está lá: `larian3/simlam-telegram-bot`)
- ✅ Conta no Render.com (criar em https://render.com)
- ✅ Banco de dados PostgreSQL (pode usar o mesmo do Koyeb ou criar novo no Render)

---

## Passo 1: Criar Conta no Render

1. Acesse https://render.com
2. Clique em **"Get Started for Free"**
3. Faça login com sua conta **GitHub** (recomendado para deploy automático)

---

## Passo 2: Criar Banco de Dados PostgreSQL (se necessário)

Se você já tem um PostgreSQL externo (ex.: Supabase, ElephantSQL), pode pular este passo.

### Opção A: PostgreSQL no Render (RECOMENDADO - Gratuito por 90 dias)

1. No dashboard do Render, clique em **"New +"** → **"PostgreSQL"**
2. Configure:
   - **Name**: `simlam-bot-db`
   - **Database**: `simlam_bot`
   - **User**: `simlam_user` (ou deixe padrão)
   - **Region**: **US East (Ohio)** ou **US West (Oregon)** ⚠️ **IMPORTANTE: Escolha região US!**
   - **PostgreSQL Version**: 15 (ou mais recente)
   - **Plan**: **Free** (válido por 90 dias)
3. Clique em **"Create Database"**
4. **Copie a Internal Database URL** (você vai precisar depois)

**Vantagens**:
- ✅ Mesma rede do Render (mais rápido)
- ✅ Sem problemas de IPv6/conexão
- ✅ Gratuito por 90 dias

### Opção B: Usar Supabase (com Connection Pooler)

Se você usa Supabase e precisa manter os dados:

1. **Acesse Supabase Dashboard** → Settings → Database
2. **Copie a URL do Transaction Pooler** (porta 6543)
3. Use essa URL no Render (não a URL direta)

⚠️ **Importante**: Se der erro de IPv6, use o **pooler** (porta 6543) em vez da URL direta.

### Opção C: Usar Banco Existente

Se já tem PostgreSQL externo (não Supabase), use a mesma `DATABASE_URL` do Koyeb.

---

## Passo 3: Criar Web Service (Bot)

1. No dashboard do Render, clique em **"New +"** → **"Web Service"**
2. Conecte seu repositório GitHub:
   - Clique em **"Connect GitHub"** (se ainda não conectou)
   - Autorize o Render a acessar seus repositórios
   - Selecione o repositório: **`larian3/simlam-telegram-bot`**
   - Clique em **"Connect"**

3. Configure o serviço:
   - **Name**: `simlam-telegram-bot`
   - **Region**: **US East (Ohio)** ou **US West (Oregon)** ⚠️ **CRÍTICO: Escolha região US!**
   - **Branch**: `main`
   - **Root Directory**: (deixe vazio)
   - **Runtime**: **Python 3**
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
   - **Plan**: **Free** (750 horas/mês - suficiente para 24/7)

4. **Variáveis de Ambiente** (clique em "Advanced" → "Add Environment Variable"):
   ```
   BOT_TOKEN=seu_token_do_telegram_aqui
   DATABASE_URL=postgresql://usuario:senha@host:porta/database
   SIMLAM_CONNECT_TIMEOUT=10
   SIMLAM_READ_TIMEOUT=90
   SIMLAM_PDF_READ_TIMEOUT=240
   PORT=8080
   ```

   ⚠️ **IMPORTANTE**: 
   - Se criou PostgreSQL no Render, use a **Internal Database URL** (mais rápido)
   - Se usa banco externo, use a URL completa com credenciais

5. Clique em **"Create Web Service"**

---

## Passo 4: Ajustar Código (se necessário)

O Render usa a variável `PORT` automaticamente. Verifique se o `bot.py` está usando:

```python
port = int(os.environ.get('PORT', 8080))
```

Seu código já deve estar assim. ✅

---

## Passo 5: Deploy e Teste

1. O Render vai fazer o deploy automaticamente após criar o serviço
2. Acompanhe os logs em tempo real no dashboard
3. Teste o bot no Telegram:
   - Envie `/start`
   - Teste `/monitorar 2025/0000016888`
   - Verifique se as verificações automáticas funcionam

---

## Passo 6: Configurar UptimeRobot (IMPORTANTE para Free Tier)

O Render Free tier **desliga automaticamente** após 15 minutos de inatividade. Para evitar isso, configure o **UptimeRobot** (gratuito) para fazer ping no seu bot a cada 5 minutos.

### Como Configurar:

1. **Obter a URL do seu serviço no Render:**
   - No dashboard do Render, vá em seu Web Service
   - Copie a URL pública (ex.: `https://simlam-telegram-bot.onrender.com`)
   - A URL completa do health check será: `https://simlam-telegram-bot.onrender.com/health`

2. **Criar conta no UptimeRobot:**
   - Acesse: https://uptimerobot.com
   - Crie uma conta gratuita (50 monitores grátis)

3. **Adicionar Monitor:**
   - Clique em **"Add New Monitor"**
   - **Monitor Type**: `HTTP(s)`
   - **Friendly Name**: `SIMLAM Bot - Render`
   - **URL (or IP)**: `https://seu-servico.onrender.com/health`
   - **Monitoring Interval**: `5 minutes` (gratuito)
   - **Alert Contacts**: (opcional) configure email/Telegram para alertas
   - Clique em **"Create Monitor"**

4. **Pronto!** ✅
   - O UptimeRobot vai fazer ping a cada 5 minutos
   - Isso mantém o serviço Render ativo 24/7
   - Você recebe alertas se o serviço cair

### Alternativas Gratuitas:

- **UptimeRobot**: 50 monitores grátis, checks a cada 5 min
- **Cronitor**: 5 monitores grátis, checks a cada 1 min
- **Pingdom**: 1 monitor grátis, checks a cada 1 min
- **StatusCake**: 10 monitores grátis, checks a cada 5 min

**Recomendação**: UptimeRobot é o mais popular e confiável.

---

## 🔧 Troubleshooting

### Erro: "Module not found"
- Verifique se `requirements.txt` está completo
- Veja os logs do build no Render

### Erro: "Database connection failed"
- Verifique se `DATABASE_URL` está correta
- Se usa PostgreSQL do Render, use a **Internal Database URL** (não a externa)

### Erro: "Port already in use"
- O Render define `PORT` automaticamente, não precisa configurar manualmente

### Bot não responde
- Verifique os logs no dashboard do Render
- Confirme que `BOT_TOKEN` está correto
- Teste o endpoint `/health` no navegador

---

## 📊 Comparação: Koyeb vs Render

| Recurso | Koyeb Free | Render Free |
|---------|-----------|-------------|
| Regiões disponíveis | Frankfurt (bloqueado) | EU + US (escolha livre) |
| Horas/mês | Ilimitado | 750h (suficiente 24/7) |
| PostgreSQL | Não incluído | 90 dias grátis |
| Deploy automático | ✅ | ✅ |
| Health checks | ✅ | ✅ |
| Logs em tempo real | ✅ | ✅ |

---

## ✅ Checklist Final

- [ ] Conta criada no Render.com
- [ ] PostgreSQL criado (ou URL externa configurada)
- [ ] Web Service criado com região **US**
- [ ] Variáveis de ambiente configuradas
- [ ] Deploy concluído com sucesso
- [ ] Bot testado no Telegram
- [ ] Verificações automáticas funcionando
- [ ] **UptimeRobot configurado** (para evitar spin down)

---

## 🎉 Pronto!

Seu bot agora está rodando no Render com região US, que provavelmente **não está bloqueada** pela SEMAS!

**Dica**: Mantenha o serviço no Koyeb por alguns dias para comparar, depois pode desligar.


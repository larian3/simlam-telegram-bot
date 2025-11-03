# 🤖 SIMLAM Telegram Bot

Um bot para Telegram projetado para automatizar a consulta e o monitoramento de processos no sistema SIMLAM (Sistema de Monitoramento e Licenciamento Ambiental) da SEMAS-PA. Receba notificações automáticas sobre novas movimentações nos seus processos de interesse.

---

## ✨ Funcionalidades

-   **🔍 Consulta Rápida:** Envie o número de um processo diretamente no chat para obter o status atual.
-   **🔔 Monitoramento Automático:** Registre processos de interesse e seja notificado a cada 15 minutos sobre qualquer atualização.
-   **⚙️ Comandos Simples:** Utilize comandos como `/monitorar`, `/listar` e `/status` para gerenciar seus processos.
-   **🚀 Verificação Paralela:** As buscas são feitas em paralelo para garantir performance, mesmo com muitos processos monitorados.
-   **💪 Resiliente a Falhas:** O bot possui mecanismos de novas tentativas para lidar com instabilidades temporárias no site da SEMAS.

---

## 🕷️ O Coração do Projeto: O Scraper (`simlam_scraper.py`)

O componente mais complexo e vital deste projeto é o scraper, responsável por navegar no site do SIMLAM, simular a interação de um usuário e extrair as informações relevantes. O site é construído com tecnologia ASP.NET Web Forms, o que torna o scraping um desafio interessante.

### O Fluxo de Scraping Detalhado

O scraper segue um fluxo de múltiplos passos para obter os dados de um único processo:

**1. Acesso à Página de Busca**
   - O scraper primeiro faz uma requisição `GET` para a página `ListarProcessos.aspx`.
   - **Desafio:** Sendo uma aplicação ASP.NET, a página contém tokens de estado essenciais (`__VIEWSTATE`, `__VIEWSTATEGENERATOR`, `__EVENTVALIDATION`) que são necessários para qualquer interação subsequente.
   - **Solução:** O scraper analisa o HTML da página com `BeautifulSoup` para extrair e armazenar esses tokens.

**2. Simulação da Busca (Requisição AJAX)**
   - Em vez de submeter um formulário tradicional, o site utiliza uma requisição `POST` assíncrona (AJAX) para realizar a busca.
   - **Desafio:** É preciso replicar exatamente o corpo (`form-data`) que o JavaScript do site enviaria, incluindo os tokens de estado e os IDs dos controles ASP.NET.
   - **Solução:** O scraper monta um payload com todos os campos necessários, incluindo `__EVENTTARGET` apontando para o botão de pesquisa, e envia a requisição. A resposta não é um HTML completo, mas um formato específico do ASP.NET AJAX.

**3. Análise da Resposta AJAX**
   - A resposta da busca é uma string longa, com campos separados por `|`.
   - **Desafio:** Encontrar a parte da resposta que contém o HTML da tabela de resultados.
   - **Solução:** Uma função (`parse_ajax_response`) analisa essa string, identifica o painel de atualização (`updatePanel`) correto e extrai o trecho de HTML com os resultados da busca.

**4. Extração do ID do Processo**
   - Com o HTML da tabela de resultados, o scraper utiliza `BeautifulSoup` novamente para encontrar o link "Visualizar".
   - **Desafio:** O link não é uma URL direta, mas uma chamada de função JavaScript, como `abrirProcesso(12345)`.
   - **Solução:** O scraper usa uma expressão regular (`regex`) para extrair o ID numérico do processo de dentro da chamada JavaScript.

**5. Acesso à Página de Detalhes e Geração do PDF (Outra Requisição AJAX)**
   - O bot constrói a URL da página de detalhes (ex: `VisualizarProcesso.aspx?id=12345`) e a acessa.
   - Assim como na primeira etapa, ele extrai os novos tokens de estado desta página.
   - **Desafio:** O link para o PDF não existe diretamente na página. Ele é gerado dinamicamente após o clique em um botão, que dispara outra requisição `POST` assíncrona.
   - **Solução:** O scraper simula essa requisição, enviando os tokens de estado corretos. A resposta AJAX contém uma chamada `window.open(...)` com a URL final do PDF.

**6. Download e Análise do PDF**
   - O scraper extrai a URL final do PDF da resposta AJAX e faz o download do conteúdo do arquivo em memória, sem precisar salvá-lo em disco.
   - **Desafio:** As informações dentro do PDF não são estruturadas. É um texto puro.
   - **Solução:** A biblioteca `PyMuPDF` (`fitz`) é utilizada para ler o conteúdo do PDF em memória e extrair todo o seu texto.

**7. Extração dos Dados Finais**
   - Com o texto completo do PDF, o scraper utiliza uma série de expressões regulares (`regex`) para encontrar e extrair cada informação relevante: número do processo, interessado, situação e, mais importante, a tabela de tramitações.
   - Os dados são limpos, estruturados em um dicionário Python e retornados para o `bot.py`.

### Tecnologias Utilizadas no Scraper
-   `requests`: Para todas as comunicações HTTP.
-   `BeautifulSoup4`: Para a análise (parsing) de HTML.
-   `PyMuPDF (fitz)`: Para a extração de texto de arquivos PDF.
-   `regex (re)`: Para a extração de informações específicas do JavaScript e do texto do PDF.

---

## 🛠️ Configuração e Instalação

Siga os passos abaixo para executar o bot localmente.

**Pré-requisitos:**
-   Python 3.10+
-   PostgreSQL (ou outro banco de dados compatível com SQLAlchemy)

**1. Clone o Repositório**
   ```bash
   git clone https://github.com/seu-usuario/simlam-telegram-bot.git
   cd simlam-telegram-bot
   ```

**2. Crie e Ative um Ambiente Virtual**
   ```bash
   # Windows
   python -m venv venv
   .\venv\Scripts\activate

   # Linux / macOS
   python3 -m venv venv
   source venv/bin/activate
   ```

**3. Instale as Dependências**
   ```bash
   pip install -r requirements.txt
   ```

**4. Configure as Variáveis de Ambiente**
   Crie um arquivo `.env` na raiz do projeto (ou configure as variáveis diretamente no seu sistema operacional) com os seguintes valores:

   ```
   BOT_TOKEN="SEU_TOKEN_DO_TELEGRAM_AQUI"
   DATABASE_URL="postgresql://usuario:senha@host:porta/nome_do_banco"
   ```

**5. Execute o Bot**
   ```bash
   python bot.py
   ```
   O bot irá iniciar, criar as tabelas no banco de dados (se não existirem) e começar a ouvir por mensagens e executar as verificações agendadas.

---

## 🐳 Rodando com Docker

Este projeto também inclui um `Dockerfile` para facilitar a implantação.

**1. Construa a Imagem Docker**
   ```bash
   docker build -t simlam-bot .
   ```

**2. Execute o Contêiner**
   Não se esqueça de passar as variáveis de ambiente para o contêiner.

   ```bash
   docker run -d \
     --name simlam-bot-container \
     -e BOT_TOKEN="SEU_TOKEN_DO_TELEGRAM_AQUI" \
     -e DATABASE_URL="URL_DO_SEU_BANCO_DE_DADOS" \
     simlam-bot
   ```
# simlam_doc_scraper.py

import requests
from bs4 import BeautifulSoup
import fitz  # PyMuPDF
import os
import re
import json
import sys
from urllib.parse import urljoin
import time
import logging
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.json import JSON
import unicodedata

console = Console()

# Configura o logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def normalize_text(text):
    """Remove acentos e caracteres especiais de um texto."""
    if not text:
        return ""
    # Normaliza para a forma NFD (Canonical Decomposition) e remove caracteres não-ASCII
    text = unicodedata.normalize('NFD', text)
    text = text.encode('ascii', 'ignore').decode('utf-8')
    return text

def parse_ajax_response(text):
    """
    Analisa a resposta AJAX do ASP.NET e extrai os painéis de atualização de HTML.
    """
    panels = {}
    parts = text.split('|')
    i = 0
    while i < len(parts) - 1:
        try:
            length = int(parts[i])
            part_type = parts[i+1]
            part_id = parts[i+2]
            content = parts[i+3]

            if part_type == 'updatePanel':
                panels[part_id] = content

            i += 4
        except (ValueError, IndexError):
            # Ignora partes malformadas da resposta AJAX
            i += 1
            continue
    return panels

def clean_despacho(text):
    """Remove o rodapé padrão do texto do despacho."""
    if text:
        # Acha o início do rodapé e corta tudo a partir dali
        footer_marker = "GOVERNO DO ESTADO DO PARÁ"
        marker_pos = text.find(footer_marker)
        if marker_pos != -1:
            return text[:marker_pos].strip()
    return text

def extract_pdf_data(full_text):
    """
    Extrai as informações de um texto de PDF e retorna um dicionário.
    Lida com múltiplos tipos de tramitação e ausência de campos.
    """
    data = {}
    
    # Limpa o texto antes de processar
    clean_text = normalize_text(full_text)

    def find_value(pattern, text):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return next((g for g in match.groups() if g is not None), None)
        return None

    # Campos principais (busca no texto limpo)
    data['numero_documento'] = find_value(r'Numero do processo:\s*([\d/]+)', clean_text)
    data['data_criacao'] = find_value(r'Data de criacao:\s*(.+)', clean_text)
    data['empreendimento'] = find_value(r'Empreendimento:\s*(.+)', clean_text)
    data['interessado'] = find_value(r'Interessado:\s*(.+)', clean_text)
    data['tipo_documento'] = find_value(r'Tipo do processo:\s*(.+)|Tipo do documento:\s*(.+)', clean_text)
    data['situacao_documento'] = find_value(r'Situacao do processo:\s*(.+)|Situacao do documento:\s*(.+)', clean_text)

    # Limpa e mantém apenas chaves com valor
    data = {k: v.strip() for k, v in data.items() if v is not None}
    data['tramitacoes'] = []

    tipos_evento_conhecidos = ["Envio", "Envio cancelado", "Mover", "Arquivamento"]
    tramitacao_headers_regex = r'\n(' + '|'.join(tipos_evento_conhecidos) + r')\n'
    
    # Usa o texto original para extrair os detalhes que podem ter caracteres especiais
    blocks = re.split(tramitacao_headers_regex, full_text, flags=re.IGNORECASE)
    content_blocks = blocks[1:]  # pula cabeçalho

    for i in range(0, len(content_blocks), 2):
        tipo_evento_original = content_blocks[i].strip()
        tipo_evento = next((t for t in tipos_evento_conhecidos if t.lower() == tipo_evento_original.lower()), None)
        if not tipo_evento:
            continue

        bloco = content_blocks[i+1]
        evento = {"tipo": tipo_evento}

        if tipo_evento == "Envio":
            evento['data_hora_envio'] = find_value(r'Data/Hora de envio:\s*(.+)', bloco)
            evento['setor_origem'] = find_value(r'Setor de origem:\s*(.+)', bloco)

            recebimento_match = re.search(r'Recebimento([\s\S]+)', bloco, re.IGNORECASE)
            if recebimento_match:
                recebimento_text = recebimento_match.group(1)
                evento['setor_destino'] = find_value(r'Setor de destino:\s*(.+)', recebimento_text)
                evento['data_hora_recebimento'] = find_value(r'Data/Hora do recebimento:\s*(.+)', recebimento_text)
                despacho_match = re.search(r'Despacho:\s*([\s\S]+?)(?=$|\n\s*\n)', recebimento_text, re.IGNORECASE)
                raw_despacho = despacho_match.group(1).replace('\n', ' ').strip() if despacho_match else None
                evento['despacho'] = clean_despacho(raw_despacho)
            else:
                despacho_match = re.search(r'Despacho:\s*([\s\S]+?)(?=\nDocumento\(s\) Juntado\(s\))', bloco, re.IGNORECASE)
                raw_despacho = despacho_match.group(1).replace('\n', ' ').strip() if despacho_match else None
                evento['despacho'] = clean_despacho(raw_despacho)
                evento['setor_destino'] = find_value(r'Setor de destino:\s*(.+)', bloco)

        elif tipo_evento == "Envio cancelado":
            evento['data_hora_cancelamento'] = find_value(r'Data/Hora de cancelamento:\s*(.+)', bloco)
            evento['motivo'] = find_value(r'Cancelado por:\s*(.+)', bloco)
            despacho_match = re.search(r'Despacho:\s*([\s\S]+)', bloco, re.IGNORECASE)
            raw_despacho = despacho_match.group(1).replace('\n', ' ').strip() if despacho_match else None
            evento['despacho'] = clean_despacho(raw_despacho)

        elif tipo_evento == "Mover":
            evento['setor_origem'] = find_value(r'Setor de origem:\s*(.+)', bloco)
            evento['setor_destino'] = find_value(r'Setor de destino:\s*(.+)', bloco)
            evento['data_hora_recebimento'] = find_value(r'Data/Hora do recebimento:\s*(.+)', bloco)
            despacho_match = re.search(r'Despacho:\s*([\s\S]+)', bloco, re.IGNORECASE)
            raw_despacho = despacho_match.group(1).replace('\n', ' ').strip() if despacho_match else None
            evento['despacho'] = clean_despacho(raw_despacho)

        elif tipo_evento == "Arquivamento":
            evento['data_hora_arquivamento'] = find_value(r'Data/Hora do arquivamento:\s*(.+)', bloco)
            evento['setor'] = find_value(r'Setor:\s*(.+)', bloco)
            despacho_match = re.search(r'Observa(?:ç|c)ão:\s*([\s\S]+)', bloco, re.IGNORECASE)
            evento['observacao'] = despacho_match.group(1).replace('\n', ' ').strip() if despacho_match else None

        evento_final = {k: v for k, v in evento.items() if v is not None}
        if len(evento_final) > 1:
            data['tramitacoes'].append(evento_final)

    return data


def print_summary_table(data):
    """
    Imprime um resumo curto em tabela (se houver dados relevantes).
    """
    table = Table(title="Resumo do Documento / Processo", show_lines=False)
    table.add_column("Campo", style="cyan", no_wrap=True)
    table.add_column("Valor", style="white")

    keys = ["numero_documento", "data_criacao", "empreendimento", "interessado", "tipo_documento", "situacao_documento"]
    any_value = False
    for k in keys:
        if k in data:
            any_value = True
            table.add_row(k, str(data.get(k, "-")))
    if any_value:
        console.print(table)


def buscar_processo(search_term, search_type="processo"):
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        logger.info(f"Iniciando busca por {search_type}: '{search_term}' (Tentativa {attempt}/{max_retries})")
        base_url = "https://monitoramento.semas.pa.gov.br/simlam/"
        if search_type == "documento":
            search_page = "ListarDocumentos.aspx"
            view_js_function = "abrirDocumento"
            view_page = "VisualizarDocumento.aspx"
        else: # processo
            search_page = "ListarProcessos.aspx"
            view_js_function = "abrirProcesso"
            view_page = "VisualizarProcesso.aspx"

        search_page_url = urljoin(base_url, search_page)

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        })

        # Define um timeout padrão para todas as requisições da sessão
        timeout = 240  

        try:
            logger.info(f"1. Acessando página de busca: {search_page_url}")
            response = session.get(search_page_url, timeout=timeout)
            response.raise_for_status()
            logger.info("Página de busca acessada com sucesso.")

            soup = BeautifulSoup(response.text, 'html.parser')

            try:
                viewstate = soup.find('input', {'name': '__VIEWSTATE'}).get('value')
                viewstategenerator = soup.find('input', {'name': '__VIEWSTATEGENERATOR'}).get('value')
                eventvalidation = soup.find('input', {'name': '__EVENTVALIDATION'}).get('value')
                logger.info("VIEWSTATE e outros tokens extraídos.")
            except AttributeError:
                logger.error("Falha ao extrair VIEWSTATE da página de busca.")
                return {'timestamp': None, 'details': "Erro: Não foi possível extrair os dados de estado da página de busca."}

            form_data = {
                'ctl00$scriptManagerMstPage': 'ctl00$baseBody$upBuscaSimples|ctl00$baseBody$btnPesquisa',
                '__EVENTTARGET': 'ctl00$baseBody$btnPesquisa',
                '__EVENTARGUMENT': '',
                '__VIEWSTATE': viewstate,
                '__VIEWSTATEGENERATOR': viewstategenerator,
                '__EVENTVALIDATION': eventvalidation,
                'ctl00$baseBody$txtBusca': search_term,
                '__ASYNCPOST': 'true',
            }
            logger.info(f"2. Enviando termo de busca: '{search_term}'")
            response = session.post(search_page_url, data=form_data, timeout=timeout)
            response.raise_for_status()
            logger.info("Busca enviada. Resposta recebida.")

            ajax_panels = parse_ajax_response(response.text)
            results_html = ajax_panels.get('ctl00_baseBody_upGrid')
            if not results_html:
                logger.warning(f"Painel de resultados 'ctl00_baseBody_upGrid' não encontrado na resposta AJAX para '{search_term}'.")
                logger.debug(f"Resposta AJAX completa: {response.text}")
                return {'timestamp': None, 'details': f"Nenhum resultado encontrado para {search_type} '{search_term}'. A estrutura da página pode ter mudado."}

            logger.info("3. Painel de resultados encontrado na resposta AJAX.")
            soup = BeautifulSoup(results_html, 'html.parser')
            visualizar_tag = soup.find('a', title='Visualizar')
            if not (visualizar_tag and visualizar_tag.has_attr('onclick')):
                logger.warning(f"Link 'Visualizar' não encontrado no HTML de resultados para '{search_term}'.")
                return {'timestamp': None, 'details': f"Nenhum resultado acionável encontrado para {search_type} '{search_term}'."}

            logger.info("4. Link 'Visualizar' encontrado.")
            match = re.search(fr'{view_js_function}\((\d+)\)', visualizar_tag['onclick'])
            if not match:
                logger.error(f"Não foi possível extrair o ID do processo do atributo onclick: {visualizar_tag['onclick']}")
                return {'timestamp': None, 'details': "Erro: Não foi possível extrair o ID do resultado."}

            entity_id = match.group(1)
            logger.info(f"5. ID do processo extraído: {entity_id}")
            details_url = urljoin(search_page_url, f"{view_page}?id={entity_id}")

            logger.info(f"6. Acessando página de detalhes: {details_url}")
            response = session.get(details_url, timeout=timeout)
            response.raise_for_status()
            logger.info("Página de detalhes acessada com sucesso.")

            soup = BeautifulSoup(response.text, 'html.parser')
            try:
                viewstate = soup.find('input', {'name': '__VIEWSTATE'}).get('value')
                viewstategenerator = soup.find('input', {'name': '__VIEWSTATEGENERATOR'}).get('value')
                eventvalidation = soup.find('input', {'name': '__EVENTVALIDATION'}).get('value')
                logger.info("VIEWSTATE da página de detalhes extraído.")
            except AttributeError:
                logger.warning("Não foi possível extrair VIEWSTATE da página de detalhes. A geração de PDF pode falhar.")
                viewstate, viewstategenerator, eventvalidation = '', '', ''
            
            pdf_form_data = {
                'ctl00$scriptManagerMstPage': 'ctl00$baseBody$updPanelMaster|ctl00$baseBody$btnGerar',
                '__EVENTTARGET': 'ctl00$baseBody$btnGerar',
                '__EVENTARGUMENT': '',
                '__VIEWSTATE': viewstate,
                '__VIEWSTATEGENERATOR': viewstategenerator,
                '__EVENTVALIDATION': eventvalidation,
                '__ASYNCPOST': 'true',
            }
            logger.info("7. Solicitando geração do PDF.")
            pdf_page_response = session.post(details_url, data=pdf_form_data, timeout=timeout)
            pdf_page_response.raise_for_status()
            logger.info("Resposta para geração do PDF recebida.")

            pdf_url = None
            match_pdf = re.search(r"window\.open\('([^']+)'", pdf_page_response.text)
            if match_pdf:
                pdf_url = urljoin(details_url, match_pdf.group(1))
                logger.info(f"8. URL do PDF encontrada via window.open: {pdf_url}")
            else:
                logger.warning("Não foi possível encontrar 'window.open' na resposta. Tentando encontrar um link <a>.")
                process_soup = BeautifulSoup(pdf_page_response.text, 'html.parser')
                pdf_link_tag = process_soup.find('a', title=re.compile(r'\.pdf$', re.IGNORECASE))
                if pdf_link_tag and pdf_link_tag.has_attr('href'):
                    pdf_url = urljoin(details_url, pdf_link_tag['href'])
                    logger.info(f"8. URL do PDF encontrada via tag <a>: {pdf_url}")

            if not pdf_url:
                logger.error("Não foi possível localizar o link do PDF na resposta do servidor.")
                return {'timestamp': None, 'details': "Erro: Não foi possível localizar o link do PDF."}

            logger.info(f"9. Baixando PDF de: {pdf_url}")
            pdf_response = session.get(pdf_url, timeout=timeout)
            pdf_response.raise_for_status()
            pdf_content = pdf_response.content
            logger.info("PDF baixado com sucesso.")

            logger.info("10. Extraindo texto do PDF.")
            doc = fitz.open(stream=pdf_content, filetype="pdf")
            full_text = "".join(page.get_text() for page in doc)
            doc.close()
            logger.info("Texto do PDF extraído.")

            final_data = extract_pdf_data(full_text)
            
            # Validação do número do processo
            pdf_process_number = final_data.get('numero_documento')
            if pdf_process_number and (search_term.replace('/', '') == pdf_process_number.replace('/', '')):
                logger.info(f"Validação bem-sucedida: o número do PDF ({pdf_process_number}) corresponde ao termo de busca.")
                
                # Validação Mínima de conteúdo
                if not final_data.get('empreendimento') and not final_data.get('tramitacoes'):
                    logger.warning(f"PDF para '{search_term}' não continha 'empreendimento' ou 'tramitacoes'. Pode ser um PDF inválido ou de erro.")
                    return {
                        'timestamp': None,
                        'details': f"Não foram encontrados detalhes suficientes para o processo '{search_term}'. O processo pode não existir ou os dados estão indisponíveis no momento."
                    }

                # Formata a saída para o bot
                output_lines = []
                output_lines.append(f"*Resumo do Processo {final_data.get('numero_documento', search_term)}*")
                output_lines.append(f"Empreendimento: {final_data.get('empreendimento', 'N/A')}")
                
                timestamp = None
                if final_data.get("tramitacoes"):
                    output_lines.append("\n*Última Tramitação:*")
                    ultima_tramitacao = final_data["tramitacoes"][-1]
                    
                    # Extrai o timestamp da última tramitação
                    timestamp = ultima_tramitacao.get('data_hora_envio') or \
                                ultima_tramitacao.get('data_hora_recebimento') or \
                                ultima_tramitacao.get('data_hora_cancelamento') or \
                                ultima_tramitacao.get('data_hora_arquivamento')

                    for key, value in ultima_tramitacao.items():
                        output_lines.append(f"- {key.replace('_', ' ').title()}: {value}")
                
                return {
                    'timestamp': timestamp,
                    'details': "\n".join(output_lines)
                }
            else:
                logger.warning(f"Divergência de processo! Buscado: '{search_term}', Encontrado no PDF: '{pdf_process_number or 'N/A'}'. Tentando novamente...")
                time.sleep(3)  # Espera 3 segundos antes da próxima tentativa
                continue # Próxima iteração do loop

        except requests.exceptions.RequestException as e:
            logger.error(f"Erro de conexão durante a busca por '{search_term}': {e}", exc_info=True)
            if attempt < max_retries:
                time.sleep(5) # Espera 5s se for erro de conexão
                continue
            return {'timestamp': None, 'details': f"Erro de conexão após {max_retries} tentativas: {e}"}
        except Exception as e:
            logger.error(f"Erro inesperado ao processar '{search_term}': {e}", exc_info=True)
            if attempt < max_retries:
                time.sleep(3)
                continue
            return {'timestamp': None, 'details': f"Ocorreu um erro inesperado após {max_retries} tentativas ao processar '{search_term}': {e}"}

    # Se o loop terminar sem sucesso
    logger.error(f"Falha ao validar o processo '{search_term}' após {max_retries} tentativas.")
    return {
        'timestamp': None,
        'details': f"Não foi possível confirmar o número do processo para '{search_term}' após {max_retries} tentativas. O site pode estar retornando resultados incorretos."
    }


def main():
    """
    Usa: python simlam_doc_scraper.py [documento|processo] [numero]
    """
    if len(sys.argv) != 3:
        console.print("[bold red]❌ Uso incorreto.[/bold red] Exemplo:", style="yellow")
        console.print("python simlam_doc_scraper.py [documento|processo] [numero]")
        return

    search_type = sys.argv[1].lower()
    search_term = sys.argv[2]

    if search_type not in ["documento", "processo"]:
        console.print("[bold red]Tipo de busca inválido.[/bold red] Use 'documento' ou 'processo'.")
        return

    console.print(Panel.fit(f"[bold cyan]Busca[/bold cyan]\nTipo: [green]{search_type}[/green]\nTermo: [blue]{search_term}[/blue]", title="Iniciando"))
    
    resultado = buscar_processo(search_term, search_type)

    # Para manter a saída rica no terminal, analisamos a string de resultado
    if resultado.get('timestamp') or "Resumo do Processo" in resultado.get('details', ''):
        # Uma forma simples de re-exibir os dados de forma rica
        # Para uma implementação real, buscar_processo poderia retornar o dict
        console.print(Panel.fit(resultado['details'], title="[bold green]Resultado[/bold green]"))
    else:
        console.print(Panel.fit(f"[bold red]Erro[/bold red]\n{resultado.get('details', 'Erro desconhecido')}", title="Erro"))


if __name__ == "__main__":
    main()

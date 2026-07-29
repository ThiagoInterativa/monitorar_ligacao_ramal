"""
Auditoria PABX Evence (Versão Otimizada e Corrigida)
===================================================
Faz login no painel via requests (sessão + CSRF), consulta os relatórios:
  - CDR sintético (/cdr/pesquisar)         -> filtra por telefone/data
  - Recusas na P.A. (/callcenter/relatorios/recusa-pa) -> coleta o período e faz o filtro local pelo Bina (telefone do cliente)
Salva tudo em SQLite local (pabx_audit.db) garantindo deduplicação estrita por Call ID / Chave composta.

CONFIGURAÇÃO DE CREDENCIAIS:
  Crie um arquivo .streamlit/secrets.toml com:
    [pabx]
    login = "seu_usuario"
    senha = "sua_senha"
    api_token = "seu_token" (opcional para conferência via API)
  Ou defina variáveis de ambiente PABX_LOGIN, PABX_SENHA e PABX_API_TOKEN.
"""

import os
import sqlite3
import unicodedata
import re
from datetime import date, datetime

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(layout="wide")
st.title("📊 Auditoria de Chamadas - PABX Evence")

BASE_URL = "https://pabx.evence.com.br"
LOGIN_URL = f"{BASE_URL}/login"
CDR_URL = f"{BASE_URL}/cdr/pesquisar"
RECUSA_PA_URL = f"{BASE_URL}/callcenter/relatorios/recusa-pa"
DB_PATH = os.path.join(os.path.dirname(__file__), "pabx_audit.db")


# ============================================================
# 1. CREDENCIAIS
# ============================================================
def get_credentials():
    try:
        return st.secrets["pabx"]["login"], st.secrets["pabx"]["senha"]
    except Exception:
        login = os.environ.get("PABX_LOGIN", "")
        senha = os.environ.get("PABX_SENHA", "")
        return login, senha


def get_api_token():
    try:
        return st.secrets["pabx"]["api_token"]
    except Exception:
        return os.environ.get("PABX_API_TOKEN", "")


# ============================================================
# 2. BANCO DE DADOS (Com UNIQUE restrito para evitar duplicidade)
# ============================================================
def init_db(reset=False):
    conn = sqlite3.connect(DB_PATH)
    if reset:
        conn.execute("DROP TABLE IF EXISTS chamadas")
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chamadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origem_relatorio TEXT,       -- 'cdr' ou 'recusa_pa'
            call_id TEXT,                -- Identificador único fornecido pelo PABX
            data_hora TEXT,
            numero_origem TEXT,
            numero_destino TEXT,
            ramal_origem TEXT,
            ramal_destino TEXT,
            tecnico TEXT,
            status TEXT,
            duracao TEXT,
            fila_id TEXT,
            raw_linha TEXT,
            coletado_em TEXT,
            UNIQUE(call_id, origem_relatorio)
        )
    """)
    conn.commit()
    return conn


def salvar_linhas(conn, linhas):
    """Insere registros evitando duplicatas via constraint UNIQUE(call_id, origem_relatorio)."""
    cur = conn.cursor()
    inseridos = 0
    for linha in linhas:
        c_id = linha.get("call_id") or f"gen_{linha.get('origem_relatorio')}_{linha.get('data_hora')}_{linha.get('numero_origem')}_{linha.get('ramal_destino')}"
        try:
            cur.execute("""
                INSERT OR IGNORE INTO chamadas
                (origem_relatorio, call_id, data_hora, numero_origem, numero_destino,
                 ramal_origem, ramal_destino, tecnico, status, duracao, fila_id,
                 raw_linha, coletado_em)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                linha.get("origem_relatorio"), c_id, linha.get("data_hora"),
                linha.get("numero_origem"), linha.get("numero_destino"),
                linha.get("ramal_origem"), linha.get("ramal_destino"),
                linha.get("tecnico"), linha.get("status"), linha.get("duracao"),
                linha.get("fila_id"), str(linha.get("raw_linha")),
                datetime.now().isoformat()
            ))
            if cur.rowcount > 0:
                inseridos += 1
        except Exception as e:
            print(f"Erro ao salvar linha: {e}")
    conn.commit()
    return inseridos


# ============================================================
# 3. AUTENTICAÇÃO E SESSÃO PABX
# ============================================================
def login_pabx():
    login, senha = get_credentials()
    if not login or not senha:
        st.error("Credenciais não configuradas. Preencha secrets.toml ou variáveis de ambiente.")
        return None

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    try:
        r = session.get(LOGIN_URL, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        csrf = soup.find("input", {"name": "_token"})
        if not csrf:
            st.error("Token CSRF não encontrado na página de login.")
            return None

        payload = {"login": login, "senha": senha, "_token": csrf["value"]}
        response = session.post(LOGIN_URL, data=payload, timeout=15)

        if response.url == LOGIN_URL or "login" in response.url:
            st.error("Login falhou. Confira seu usuário e senha.")
            return None

        return session
    except requests.RequestException as e:
        st.error(f"Erro de conexão no login: {e}")
        return None


def get_session():
    session = st.session_state.get("session_pabx")
    if session is None:
        session = login_pabx()
        st.session_state.session_pabx = session
    return session


def fetch_with_relogin(url):
    session = get_session()
    if not session:
        return None
    resp = session.get(url, timeout=20)
    if "login" in resp.url:
        session = login_pabx()
        st.session_state.session_pabx = session
        if not session:
            return None
        resp = session.get(url, timeout=20)
    return resp


# ============================================================
# 4. NORMALIZAÇÃO E SCRAPING DE TABELAS
# ============================================================
def _normalizar(texto):
    """Remove acentos, espaços extras e converte para minúsculas para match seguro de colunas."""
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return texto.strip().lower()


def extrair_tabela(html):
    """Lê o HTML, identifica os cabeçalhos do thead e retorna dicionários mapeados com '_raw'."""
    soup = BeautifulSoup(html, "html.parser")
    tabela = soup.find("table")
    if not tabela:
        return [], 1

    thead = tabela.find("thead")
    if thead:
        cabecalhos = [_normalizar(th.get_text()) for th in thead.find_all("th")]
    else:
        primeira_linha = tabela.find("tr")
        if not primeira_linha:
            return [], 1
        cabecalhos = [_normalizar(td.get_text()) for td in primeira_linha.find_all(["th", "td"])]

    corpo = tabela.find("tbody") or tabela
    linhas_html = corpo.find_all("tr")

    registros = []
    for linha in linhas_html:
        celulas = linha.find_all("td")
        if not celulas:
            continue
        valores = [c.get_text().strip() for c in celulas]
        if len(valores) != len(cabecalhos):
            # Linha desalinhada com o cabeçalho (ex: linhas de resumo)
            continue
        registro = dict(zip(cabecalhos, valores))
        registro["_raw"] = valores
        registros.append(registro)

    # Identificação de paginação
    ultima_pagina = 1
    paginacao = soup.find("ul", class_="pagination")
    if paginacao:
        numeros = []
        for a in paginacao.find_all("a"):
            try:
                numeros.append(int(a.text.strip()))
            except ValueError:
                pass
        if numeros:
            ultima_pagina = max(numeros)

    return registros, ultima_pagina


def buscar_paginado(url_base):
    resp = fetch_with_relogin(url_base)
    if resp is None or resp.status_code != 200:
        st.error(f"Erro ao acessar relatório na URL: {url_base}")
        return []

    registros, ultima_pagina = extrair_tabela(resp.text)

    for page in range(2, ultima_pagina + 1):
        separator = "&" if "?" in url_base else "?"
        resp = fetch_with_relogin(f"{url_base}{separator}page={page}")
        if resp is None:
            continue
        novos, _ = extrair_tabela(resp.text)
        registros.extend(novos)

    return registros


# ============================================================
# 5. PARSERS ESPECÍFICOS POR RELATÓRIO
# ============================================================
def parse_tecnico_ramal(texto_destino):
    """Separa 'Gabriel Tomaz < 108 >' em ('Gabriel Tomaz', '108')."""
    if not texto_destino:
        return None, None
    match = re.search(r"^(.*?)\s*<\s*(\d+)\s*>$", texto_destino)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return texto_destino, None


def consultar_cdr(numero, data_inicial, data_final, tipo_chamada="IN"):
    url = (f"{CDR_URL}?ramal_origem=&numero_origem={numero}&ramal_destino="
           f"&numero_destino=&did=&status_chamada=&centrocusto_id=&tipo_chamada={tipo_chamada}"
           f"&gravacao=&discador=0&data_inicial={data_inicial}&data_final={data_final}")
    
    registros = buscar_paginado(url)
    linhas = []
    for r in registros:
        dest_bruto = r.get("destino") or r.get("ramal destino") or r.get("ramal")
        tecnico, ramal_dest = parse_tecnico_ramal(dest_bruto)
        
        linhas.append({
            "origem_relatorio": "cdr",
            "call_id": r.get("call id") or r.get("id"),
            "data_hora": r.get("data") or r.get("data/hora") or r.get("data hora"),
            "numero_origem": r.get("origem") or numero,
            "numero_destino": r.get("destino_numero") or None,
            "ramal_origem": r.get("ramal origem"),
            "ramal_destino": ramal_dest,
            "tecnico": tecnico,
            "status": r.get("status") or r.get("status chamada"),
            "duracao": r.get("duracao") or r.get("tempo"),
            "fila_id": r.get("fila"),
            "raw_linha": r.get("_raw"),
        })
    return linhas


def consultar_recusa_pa(fila_id, data_inicial, data_final, numero_filtro=None):
    """
    Consulta o relatório de recusas na P.A. 
    O relatório da Evence retorna TODAS as recusas da fila no período. 
    Fazemos o filtro local pelo campo 'bina' (número do cliente) para precisão cirúrgica.
    """
    url = f"{RECUSA_PA_URL}?fila_id={fila_id}&data_inicial={data_inicial}&data_final={data_final}"
    registros = buscar_paginado(url)

    linhas = []
    for r in registros:
        bina = r.get("bina") or r.get("numero") or r.get("cliente") or r.get("origem")
        
        # Se foi passado um número para busca, aplicamos o filtro exato no Bina
        if numero_filtro and numero_filtro.strip():
            if not bina or numero_filtro.strip() not in bina:
                continue

        tecnico_val = r.get("agente") or r.get("tecnico")
        
        linhas.append({
            "origem_relatorio": "recusa_pa",
            "call_id": r.get("call id") or r.get("id"),
            "data_hora": r.get("data") or r.get("data/hora"),
            "numero_origem": bina,
            "numero_destino": None,
            "ramal_origem": None,
            "ramal_destino": r.get("ramal"),
            "tecnico": tecnico_val,
            "status": "recusada_na_pa",
            "duracao": r.get("duracao") or r.get("tempo"),
            "fila_id": fila_id,
            "raw_linha": r.get("_raw"),
        })
    return linhas


# ============================================================
# 6. HEURÍSTICA DE CLASSIFICAÇÃO
# ============================================================
LIMITE_RECUSA_SEGUNDOS = st.sidebar.number_input(
    "Limite (s) para 'recusa manual'", value=3, min_value=0
)


def duracao_para_segundos(txt):
    if not txt:
        return None
    try:
        partes = [int(p) for p in txt.split(":")]
        while len(partes) < 3:
            partes.insert(0, 0)
        h, m, s = partes
        return h * 3600 + m * 60 + s
    except (ValueError, AttributeError):
        return None


def classificar(duracao_txt):
    seg = duracao_para_segundos(duracao_txt)
    if seg is None:
        return "indeterminado"
    if seg <= LIMITE_RECUSA_SEGUNDOS:
        return "provável recusa manual"
    return "tocou até esgotar"


# ============================================================
# 7. INTERFACE STREAMLIT (Abas)
# ============================================================
conn = init_db()

# Ferramenta de reset de banco na barra lateral para evitar resíduos antigos
if st.sidebar.button("⚠️ Resetar Banco Local"):
    conn.close()
    init_db(reset=True)
    conn = init_db()
    st.sidebar.success("Banco limpo com sucesso!")

tab_busca, tab_coleta, tab_mensal = st.tabs(
    ["🔎 Auditoria por Telefone", "⬇️ Coletar Dados", "📅 Fechamento Mensal"]
)

with tab_coleta:
    st.subheader("Coleta Automatizada de Relatórios (CDR + Recusas P.A.)")
    col1, col2 = st.columns(2)
    with col1:
        numero = st.text_input(
            "Telefone do Cliente (Ex: 1143820682) — Deixe vazio para puxar todo o dia",
            "1143820682"
        )
        fila_id = st.text_input("Fila ID (Ex: 2812 ou vazio)", "2812")
    with col2:
        data_inicio = st.date_input("Data Início", value=date(2026, 7, 28))
        data_fim = st.date_input("Data Fim", value=date(2026, 7, 28))

    tipo_chamada = st.selectbox("Tipo de Chamada (CDR)", ["IN", "OUT", ""], index=0)

    if st.button("Executar Coleta e Salvar"):
        di_cdr, df_cdr = data_inicio.strftime("%d-%m-%Y"), data_fim.strftime("%d-%m-%Y")
        di_pa, df_pa = data_inicio.strftime("%Y-%m-%d"), data_fim.strftime("%Y-%m-%d")

        with st.spinner("Buscando CDR Sintético..."):
            linhas_cdr = consultar_cdr(numero, di_cdr, df_cdr, tipo_chamada=tipo_chamada)
            
        with st.spinner("Buscando Recusas na P.A. (com filtro no Bina)..."):
            linhas_recusa = consultar_recusa_pa(fila_id, di_pa, df_pa, numero_filtro=numero)

        total_salvos = salvar_linhas(conn, linhas_cdr + linhas_recusa)
        st.success(f"Coleta finalizada! {total_salvos} novos registros inseridos (duplicatas bloqueadas automaticamente).")
        
        combined = linhas_cdr + linhas_recusa
        if combined:
            st.dataframe(pd.DataFrame(combined))
        else:
            st.info("Nenhum registro retornado para os filtros informados.")

with tab_busca:
    st.subheader("Timeline e Auditoria Completa por Número")
    numero_busca = st.text_input("Digite o telefone do cliente para rastrear:", "1143820682")
    
    if st.button("Gerar Auditoria do Número"):
        df_busca = pd.read_sql_query(
            "SELECT * FROM chamadas WHERE numero_origem LIKE ? OR numero_destino LIKE ? ORDER BY data_hora",
            conn, params=(f"%{numero_busca}%", f"%{numero_busca}%")
        )
        if df_busca.empty:
            st.warning("Nenhum registro encontrado para este número na base local. Realize a coleta na aba ao lado.")
        else:
            df_busca["classificacao_estimada"] = df_busca["duracao"].apply(classificar)
            st.success(f"Encontrados {len(df_busca)} eventos para o número {numero_busca}:")
            st.dataframe(df_busca[[
                "data_hora", "origem_relatorio", "tecnico", "ramal_destino",
                "status", "duracao", "classificacao_estimada", "call_id"
            ]])

with tab_mensal:
    st.subheader("Fechamento Mensal por Técnico")
    mes = st.text_input("Filtro de Mês (Formato: YYYY-MM ou DD-MM-YYYY, ex: 2026-07)", "2026-07")
    
    query = "SELECT * FROM chamadas"
    params = ()
    if mes:
        query += " WHERE data_hora LIKE ?"
        params = (f"%{mes}%",)
        
    df_mes = pd.read_sql_query(query, conn, params=params)

    if df_mes.empty:
        st.warning("Nenhum dado encontrado para o período.")
    else:
        resumo = df_mes.groupby(["tecnico", "status"]).size().reset_index(name="quantidade")
        st.dataframe(resumo)

        fig = px.bar(
            resumo, x="tecnico", y="quantidade", color="status",
            title="Desempenho de Chamadas por Técnico e Status", barmode="group"
        )
        st.plotly_chart(fig, use_container_width=True)

        csv = df_mes.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Baixar Relatório Mensal em CSV", csv,
            "relatorio_mensal_pabx.csv", "text/csv"
        )

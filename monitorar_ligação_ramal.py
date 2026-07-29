"""
Auditoria PABX Evence
======================
Faz login no painel (sessão + CSRF), consulta os relatórios:
  - CDR sintético (/cdr/pesquisar)         -> filtra por telefone/data
  - Recusas na P.A. (/callcenter/relatorios/recusa-pa) -> filtra por fila/data
Salva tudo em SQLite local (pabx_audit.db) para permitir:
  - busca de auditoria por telefone (histórico completo do número)
  - fechamento mensal por técnico (taxa de recusa/abandono, tempo médio)

CONFIGURAÇÃO DE CREDENCIAIS (não coloque senha no código!):
  Crie um arquivo .streamlit/secrets.toml com:
    [pabx]
    login = "seu_usuario"
    senha = "sua_senha"
  Ou defina as variáveis de ambiente PABX_LOGIN e PABX_SENHA.
"""

import os
import sqlite3
import unicodedata
from datetime import date, datetime

import pandas as pd
import plotly.express as px
import requests
import streamlit as st
from bs4 import BeautifulSoup

st.set_page_config(layout="wide")
st.title("📊 Auditoria de Chamadas - PABX")

BASE_URL = "https://pabx.evence.com.br"
LOGIN_URL = f"{BASE_URL}/login"
CDR_URL = f"{BASE_URL}/cdr/pesquisar"
RECUSA_PA_URL = f"{BASE_URL}/callcenter/relatorios/recusa-pa"
DB_PATH = os.path.join(os.path.dirname(__file__), "pabx_audit.db")


# ============================================================
# CREDENCIAIS (nunca hardcode - use secrets.toml ou env vars)
# ============================================================
def get_credentials():
    try:
        return st.secrets["pabx"]["login"], st.secrets["pabx"]["senha"]
    except Exception:
        login = os.environ.get("PABX_LOGIN", "")
        senha = os.environ.get("PABX_SENHA", "")
        return login, senha


# ============================================================
# BANCO DE DADOS
# ============================================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chamadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origem_relatorio TEXT,      -- 'cdr' ou 'recusa_pa'
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
            UNIQUE(origem_relatorio, data_hora, numero_origem, numero_destino, ramal_destino)
        )
    """)
    conn.commit()
    return conn


def salvar_linhas(conn, linhas):
    """linhas: lista de dicts com as chaves da tabela"""
    cur = conn.cursor()
    for linha in linhas:
        cur.execute("""
            INSERT OR IGNORE INTO chamadas
            (origem_relatorio, data_hora, numero_origem, numero_destino,
             ramal_origem, ramal_destino, tecnico, status, duracao, fila_id,
             raw_linha, coletado_em)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            linha.get("origem_relatorio"), linha.get("data_hora"),
            linha.get("numero_origem"), linha.get("numero_destino"),
            linha.get("ramal_origem"), linha.get("ramal_destino"),
            linha.get("tecnico"), linha.get("status"), linha.get("duracao"),
            linha.get("fila_id"), str(linha.get("raw_linha")),
            datetime.now().isoformat()
        ))
    conn.commit()


# ============================================================
# LOGIN / SESSÃO
# ============================================================
def login_pabx():
    login, senha = get_credentials()
    if not login or not senha:
        st.error("Credenciais não configuradas. Preencha secrets.toml ou variáveis de ambiente PABX_LOGIN/PABX_SENHA.")
        return None

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    try:
        r = session.get(LOGIN_URL, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        csrf = soup.find("input", {"name": "_token"})
        if not csrf:
            st.error("Não encontrei o token CSRF na página de login. O layout pode ter mudado.")
            return None

        payload = {"login": login, "senha": senha, "_token": csrf["value"]}
        response = session.post(LOGIN_URL, data=payload, timeout=15)

        if response.url == LOGIN_URL or "login" in response.url:
            st.error("Login falhou. Confira usuário/senha.")
            return None

        return session
    except requests.RequestException as e:
        st.error(f"Erro de conexão no login: {e}")
        return None


def get_session():
    """Garante sessão válida, refazendo login se necessário."""
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
# SCRAPER GENÉRICO DE TABELA (mapeia por nome de coluna, não índice)
# ============================================================
def _normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return texto.strip().lower()


def extrair_tabela(html):
    """Retorna lista de dicts {nome_coluna_normalizado: valor} lendo o thead."""
    soup = BeautifulSoup(html, "html.parser")
    tabela = soup.find("table")
    if not tabela:
        return [], 1

    thead = tabela.find("thead")
    if thead:
        cabecalhos = [_normalizar(th.get_text()) for th in thead.find_all("th")]
    else:
        primeira_linha = tabela.find("tr")
        cabecalhos = [_normalizar(td.get_text()) for td in primeira_linha.find_all(["th", "td"])]

    corpo = tabela.find("tbody") or tabela
    linhas_html = corpo.find_all("tr")

    registros = []
    for linha in linhas_html:
        celulas = linha.find_all("td")
        if not celulas:
            continue
        valores = [c.get_text().strip() for c in celulas]
        registro = dict(zip(cabecalhos, valores))
        registro["_raw"] = valores
        registros.append(registro)

    # paginação
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
        st.error("Erro ao acessar o relatório (verifique login/URL).")
        return []

    registros, ultima_pagina = extrair_tabela(resp.text)

    for page in range(2, ultima_pagina + 1):
        resp = fetch_with_relogin(f"{url_base}&page={page}")
        if resp is None:
            continue
        novos, _ = extrair_tabela(resp.text)
        registros.extend(novos)

    return registros


# ============================================================
# CONSULTAS ESPECÍFICAS
# ============================================================
def consultar_cdr(numero, data_inicial, data_final, ramal_destino="", tipo_chamada="IN"):
    """tipo_chamada: 'IN' = recebidas, 'OUT' = originadas, '' = todas.
    Deixe numero_origem vazio para trazer TODAS as recebidas do dia
    (útil pra auditoria geral, não só de um telefone específico)."""
    url = (f"{CDR_URL}?ramal_origem=&numero_origem={numero}&ramal_destino={ramal_destino}"
           f"&numero_destino=&did=&status_chamada=&centrocusto_id=&tipo_chamada={tipo_chamada}"
           f"&gravacao=&discador=0&data_inicial={data_inicial}&data_final={data_final}")
    registros = buscar_paginado(url)

    linhas = []
    for r in registros:
        linhas.append({
            "origem_relatorio": "cdr",
            "data_hora": r.get("data") or r.get("data/hora") or r.get("data hora"),
            "numero_origem": r.get("origem") or numero,
            "numero_destino": r.get("destino"),
            "ramal_origem": r.get("ramal origem"),
            "ramal_destino": r.get("ramal destino") or r.get("ramal"),
            "tecnico": r.get("agente") or r.get("tecnico"),
            "status": r.get("status") or r.get("status chamada"),
            "duracao": r.get("duracao") or r.get("tempo"),
            "fila_id": r.get("fila"),
            "raw_linha": r.get("_raw"),
        })
    return linhas


def consultar_recusa_pa(fila_id, data_inicial, data_final):
    url = f"{RECUSA_PA_URL}?fila_id={fila_id}&data_inicial={data_inicial}&data_final={data_final}"
    registros = buscar_paginado(url)

    linhas = []
    for r in registros:
        linhas.append({
            "origem_relatorio": "recusa_pa",
            "data_hora": r.get("data") or r.get("data/hora"),
            "numero_origem": r.get("numero") or r.get("cliente") or r.get("origem"),
            "numero_destino": None,
            "ramal_origem": None,
            "ramal_destino": r.get("ramal"),
            "tecnico": r.get("agente") or r.get("tecnico"),
            "status": "recusada_ou_nao_atendida",
            "duracao": r.get("duracao") or r.get("tempo"),
            "fila_id": fila_id,
            "raw_linha": r.get("_raw"),
        })
    return linhas


# ============================================================
# API OFICIAL (token) - contadores em tempo real da fila
# ============================================================
def get_api_token():
    try:
        return st.secrets["pabx"]["api_token"]
    except Exception:
        return os.environ.get("PABX_API_TOKEN", "")


def consultar_api_queue_stats(fila_id):
    """Usa /api/v1/queues/stats (com api_token) para pegar os contadores
    oficiais da fila (atendidas, abandonadas, TME, TMA). Serve como
    conferência: se os contadores oficiais não baterem com o que o
    scraping dos relatórios trouxe, há algo a investigar na coleta."""
    token = get_api_token()
    if not token:
        st.warning("api_token não configurado (secrets.toml -> [pabx] api_token = '...') — pulando conferência via API.")
        return None
    url = f"{BASE_URL}/api/v1/queues/stats?api_token={token}&queue={fila_id}"
    try:
        resp = requests.get(url, timeout=15)
        data = resp.json()
        if "error" in data:
            st.error(f"Erro da API: {data['error']}")
            return None
        return data.get("queueList")
    except requests.RequestException as e:
        st.error(f"Erro ao consultar API: {e}")
        return None


# ============================================================
# CLASSIFICAÇÃO recusada vs abandono/timeout (heurística por duração)
# ============================================================
LIMITE_RECUSA_SEGUNDOS = st.sidebar.number_input(
    "Limite (s) p/ considerar 'recusa manual' (abaixo disso)", value=3, min_value=0
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
    return "tocou até esgotar (não atendida)"


# ============================================================
# UI
# ============================================================
conn = init_db()

tab_busca, tab_coleta, tab_mensal = st.tabs(
    ["🔎 Auditoria por telefone", "⬇️ Coletar dados", "📅 Fechamento mensal"]
)

with tab_coleta:
    st.subheader("Coletar dados dos relatórios e salvar no banco local")
    col1, col2 = st.columns(2)
    with col1:
        numero = st.text_input(
            "Telefone do cliente (numero_origem no CDR) — deixe vazio para trazer TODAS as recebidas do dia",
            ""
        )
        fila_id = st.text_input("Fila ID", "2812")
    with col2:
        data_inicio = st.date_input("Data início", value=date.today())
        data_fim = st.date_input("Data fim", value=date.today())

    tipo_chamada = st.selectbox("Tipo de chamada (CDR)", ["IN", "OUT", ""], index=0,
                                 help="IN = recebidas (o que você quer auditar), OUT = originadas, vazio = todas")

    if st.button("Buscar e salvar"):
        di, df = data_inicio.strftime("%d-%m-%Y"), data_fim.strftime("%d-%m-%Y")
        with st.spinner("Consultando CDR sintético..."):
            linhas_cdr = consultar_cdr(numero, di, df, tipo_chamada=tipo_chamada)
        with st.spinner("Consultando recusas na P.A..."):
            di2, df2 = data_inicio.strftime("%Y-%m-%d"), data_fim.strftime("%Y-%m-%d")
            linhas_recusa = consultar_recusa_pa(fila_id, di2, df2)

        salvar_linhas(conn, linhas_cdr + linhas_recusa)
        st.success(f"Salvo: {len(linhas_cdr)} linhas de CDR + {len(linhas_recusa)} linhas de recusa-PA")
        st.dataframe(pd.DataFrame(linhas_cdr + linhas_recusa))

        with st.spinner("Conferindo contadores oficiais via API..."):
            stats = consultar_api_queue_stats(fila_id)
        if stats:
            st.subheader("Conferência (API oficial /queues/stats — dados atuais da fila)")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Atendidas (total)", stats.get("totalChamadasAtendidas", "-"))
            c2.metric("Abandonadas (total)", stats.get("totalChamadasAbandonadas", "-"))
            c3.metric("TME", stats.get("TME", "-"))
            c4.metric("TMA", stats.get("TMA", "-"))
            st.caption("Nota: a API traz contadores acumulados atuais da fila, não filtrados por data — "
                       "use apenas como conferência de consistência, não como fonte do relatório mensal.")

with tab_busca:
    st.subheader("Timeline completa de um número")
    numero_busca = st.text_input("Telefone para auditar", key="busca_telefone")
    if st.button("Auditar número"):
        df = pd.read_sql_query(
            "SELECT * FROM chamadas WHERE numero_origem LIKE ? OR numero_destino LIKE ? ORDER BY data_hora",
            conn, params=(f"%{numero_busca}%", f"%{numero_busca}%")
        )
        if df.empty:
            st.warning("Nenhum registro encontrado para esse número (colete os dados na aba anterior primeiro).")
        else:
            df["classificacao_estimada"] = df["duracao"].apply(classificar)
            st.dataframe(df[["data_hora", "origem_relatorio", "tecnico", "ramal_destino",
                              "status", "duracao", "classificacao_estimada"]])

with tab_mensal:
    st.subheader("Resumo mensal por técnico")
    mes = st.text_input("Filtro de mês (formato livre, ex: 2026-07)", "")
    query = "SELECT * FROM chamadas"
    params = ()
    if mes:
        query += " WHERE data_hora LIKE ?"
        params = (f"%{mes}%",)
    df = pd.read_sql_query(query, conn, params=params)

    if df.empty:
        st.warning("Sem dados coletados ainda.")
    else:
        resumo = df.groupby(["tecnico", "status"]).size().reset_index(name="quantidade")
        st.dataframe(resumo)

        fig = px.bar(resumo, x="tecnico", y="quantidade", color="status",
                     title="Chamadas por técnico e status", barmode="group")
        st.plotly_chart(fig, use_container_width=True)

        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("Baixar CSV para planilha de desempenho", csv,
                            "relatorio_mensal_pabx.csv", "text/csv")

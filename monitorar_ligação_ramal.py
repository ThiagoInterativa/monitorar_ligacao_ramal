"""
Auditoria PABX Evence (Versão Cruzada Completa)
================================================
Cruza dados de:
  - API de CDR (/api/v1/cdr)
  - Relatório Web Sintético (/cdr/pesquisar)
  - Relatório Web de Recusas na P.A. (/callcenter/relatorios/recusa-pa)
Monta a linha do tempo unificada e ordenada por cronologia para qualquer número de telefone.
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
st.title("📊 Auditoria Unificada de Chamadas - PABX Evence")

BASE_URL = "https://pabx.evence.com.br"
LOGIN_URL = f"{BASE_URL}/login"
CDR_URL = f"{BASE_URL}/cdr/pesquisar"
RECUSA_PA_URL = f"{BASE_URL}/callcenter/relatorios/recusa-pa"
API_CDR_URL = f"{BASE_URL}/api/v1/cdr"
DB_PATH = os.path.join(os.path.dirname(__file__), "pabx_audit.db")


# ============================================================
# 1. CREDENCIAIS E TOKENS
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
        return os.environ.get("PABX_API_TOKEN", "4275c3fd79ac7997e3dc03fb451657518b50d55203c41c8798a3c81eb5825031")


# ============================================================
# 2. BANCO DE DADOS
# ============================================================
def init_db(reset=False):
    conn = sqlite3.connect(DB_PATH)
    if reset:
        conn.execute("DROP TABLE IF EXISTS chamadas")
    
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chamadas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origem_relatorio TEXT,        -- 'api_cdr', 'cdr_web' ou 'recusa_pa'
            call_id TEXT,                 -- Identificador único do salto/chamada
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
            UNIQUE(call_id, origem_relatorio, data_hora)
        )
    """)
    conn.commit()
    return conn


def salvar_linhas(conn, linhas):
    cur = conn.cursor()
    inseridos = 0
    for linha in linhas:
        c_id = linha.get("call_id") or f"gen_{linha.get('origem_relatorio')}_{linha.get('data_hora')}_{linha.get('numero_origem')}"
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
# 4. PARSERS E NORMALIZAÇÃO DE TABELAS WEB
# ============================================================
def _normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return texto.strip().lower()


def extrair_tabela(html):
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
        celulas = linha.find_all(["td", "th"])
        if not celulas:
            continue
        valores = [c.get_text().strip() for c in celulas]
        
        # Mapeamento híbrido: tenta por nome e cria colunas posicionais col_0, col_1...
        registro = {}
        if len(valores) == len(cabecalhos):
            registro = dict(zip(cabecalhos, valores))
            
        for i, val in enumerate(valores):
            registro[f"col_{i}"] = val
            
        registro["_raw"] = valores
        registros.append(registro)

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


def parse_tecnico_ramal(texto_destino):
    if not texto_destino:
        return None, None
    
    # Se o texto for puramente um número de telefone ou ramal puro (somente dígitos longos), não é um nome de técnico
    if texto_destino.isdigit() and len(texto_destino) > 4:
        return None, None

    match = re.search(r"^(.*?)\s*<\s*(\d+)\s*>$", texto_destino)
    if match:
        nome = match.group(1).strip()
        ramal = match.group(2).strip()
        if nome.isdigit() and len(nome) > 4:
            return None, ramal
        return nome, ramal
    
    if texto_destino.isdigit():
        return None, texto_destino
        
    return texto_destino, None


# ============================================================
# 5. COLETA DAS 3 FONTES
# ============================================================
def consultar_api_cdr(data_inicio_str, data_fim_str):
    token = get_api_token()
    if not token:
        return []
    
    indice = 0
    linhas = []
    while True:
        url = f"{API_CDR_URL}?api_token={token}&datainicio={data_inicio_str}&datafinal={data_fim_str}&indice={indice}"
        try:
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                break
            data = resp.json()
            if "error" in data:
                break
            
            cdr_dict = data.get("cdr", data)
            if not cdr_dict or len(cdr_dict) == 0:
                break
            
            itens = cdr_dict.values() if isinstance(cdr_dict, dict) else cdr_dict
            for reg in itens:
                if isinstance(reg, (list, tuple)) and len(reg) >= 6:
                    linhas.append({
                        "origem_relatorio": "api_cdr",
                        "call_id": f"api_{reg[0]}_{reg[1]}_{reg[2]}",
                        "data_hora": str(reg[0]),
                        "numero_origem": str(reg[1]),
                        "numero_destino": str(reg[2]),
                        "ramal_origem": None,
                        "ramal_destino": None,
                        "tecnico": None,
                        "status": str(reg[5]),
                        "duracao": str(reg[4]),
                        "fila_id": None,
                        "raw_linha": list(reg)
                    })
            
            qtd = len(cdr_dict)
            if qtd == 0:
                break
            indice += qtd
        except Exception:
            break
    return linhas


def consultar_cdr_web(numero, data_inicial, data_final, tipo_chamada="IN"):
    url = (f"{CDR_URL}?ramal_origem=&numero_origem={numero}&ramal_destino="
           f"&numero_destino=&did=&status_chamada=&centrocusto_id=&tipo_chamada={tipo_chamada}"
           f"&gravacao=&discador=0&data_inicial={data_inicial}&data_final={data_final}")
    
    registros = buscar_paginado(url)
    linhas = []
    for r in registros:
        # Busca o destino bruto de colunas web comuns ou posicionais
        dest_bruto = (
            r.get("destino") or r.get("ramal destino") or r.get("ramal") or 
            r.get("col_3") or r.get("col_4") or r.get("col_5")
        )
        tecnico, ramal_dest = parse_tecnico_ramal(dest_bruto)
        
        # Garante que números de telefone capturados acidentalmente não passem como nome de técnico
        if tecnico and tecnico.isdigit():
            tecnico = None

        data_hora = (
            r.get("data") or r.get("data/hora") or r.get("data hora") or 
            r.get("col_0") or r.get("col_1")
        )
        call_id = r.get("call id") or r.get("id") or r.get("call_id")
        status_val = r.get("status") or r.get("status chamada") or r.get("col_6")
        duracao_val = r.get("duracao") or r.get("tempo") or r.get("col_7")

        linhas.append({
            "origem_relatorio": "cdr_web",
            "call_id": call_id,
            "data_hora": data_hora,
            "numero_origem": r.get("origem") or numero,
            "numero_destino": r.get("destino_numero") or None,
            "ramal_origem": r.get("ramal origem"),
            "ramal_destino": ramal_dest,
            "tecnico": tecnico,
            "status": status_val,
            "duracao": duracao_val,
            "fila_id": r.get("fila"),
            "raw_linha": r.get("_raw"),
        })
    return linhas


def consultar_recusa_pa_web(fila_id, data_inicial, data_final, numero_filtro=None):
    url = f"{RECUSA_PA_URL}?fila_id={fila_id}&data_inicial={data_inicial}&data_final={data_final}"
    registros = buscar_paginado(url)

    linhas = []
    for r in registros:
        data_hora = r.get("data/hora") or r.get("data") or r.get("col_0")
        fila_val = r.get("fila") or r.get("col_1") or fila_id
        agente = r.get("agente") or r.get("tecnico") or r.get("col_2")
        call_id = r.get("call id") or r.get("id") or r.get("col_3")
        bina = r.get("bina") or r.get("numero") or r.get("cliente") or r.get("origem") or r.get("col_4")
        duracao = r.get("duracao") or r.get("tempo") or r.get("col_5")

        if numero_filtro and numero_filtro.strip():
            if not bina or numero_filtro.strip() not in str(bina):
                continue

        linhas.append({
            "origem_relatorio": "recusa_pa",
            "call_id": call_id,
            "data_hora": data_hora,
            "numero_origem": bina,
            "numero_destino": None,
            "ramal_origem": None,
            "ramal_destino": None,
            "tecnico": agente,
            "status": f"Recusada / Fila {fila_val}",
            "duracao": duracao,
            "fila_id": fila_val,
            "raw_linha": r.get("_raw"),
        })
    return linhas


# ============================================================
# 6. INTERFACE STREAMLIT
# ============================================================
conn = init_db()

if st.sidebar.button("⚠️ Resetar Base de Dados"):
    conn.close()
    init_db(reset=True)
    conn = init_db()
    st.sidebar.success("Base limpa!")

tab_busca, tab_coleta, tab_mensal = st.tabs(
    ["🔎 Auditoria por Telefone (Histórico Cruzado)", "⬇️ Coletar Todas as Fontes", "📅 Fechamento Mensal"]
)

with tab_coleta:
    st.subheader("Varredura e Sincronização Consolidada")
    col1, col2 = st.columns(2)
    with col1:
        numero_input = st.text_input("Número do Cliente (Ex: 1143820682)", "1143820682")
        fila_id_input = st.text_input("Fila ID para Recusas P.A. (Ex: 2812)", "2812")
    with col2:
        data_inicio = st.date_input("Data Início", value=date(2026, 7, 28))
        data_fim = st.date_input("Data Fim", value=date(2026, 7, 28))

    if st.button("🚀 Sincronizar e Cruzar Dados (API + CDR Web + Recusas P.A.)"):
        di_br = data_inicio.strftime("%d-%m-%Y")
        df_br = data_fim.strftime("%d-%m-%Y")
        di_iso = data_inicio.strftime("%Y-%m-%d")
        df_iso = data_fim.strftime("%Y-%m-%d")

        total_inserido = 0
        with st.spinner("Consultando API Oficial de CDR..."):
            l_api = consultar_api_cdr(di_iso, df_iso)
            total_inserido += salvar_linhas(conn, l_api)

        with st.spinner("Consultando CDR Sintético Web..."):
            l_web = consultar_cdr_web(numero_input, di_br, df_br)
            total_inserido += salvar_linhas(conn, l_web)

        with st.spinner("Consultando Relatório de Recusas na P.A. Web..."):
            l_recusa = consultar_recusa_pa_web(fila_id_input, di_iso, df_iso, numero_filtro=numero_input)
            total_inserido += salvar_linhas(conn, l_recusa)

        st.success(f"Sincronização concluída! {total_inserido} novos registros cruzados e salvos.")

with tab_busca:
    st.subheader("🗺️ Histórico Unificado e Cruzado da Ligação")
    st.markdown("Pesquise o número para visualizar a rota completa (Atendidas, Abandonadas, Recusas Intercom / P.A.).")
    
    telefone_busca = st.text_input("Número do Telefone para Auditoria:", "1143820682")
    
    if st.button("🔍 Gerar Histórico Cronológico"):
        df_busca = pd.read_sql_query(
            "SELECT * FROM chamadas WHERE numero_origem LIKE ? OR numero_destino LIKE ? OR raw_linha LIKE ? ORDER BY data_hora ASC",
            conn, params=(f"%{telefone_busca}%", f"%{telefone_busca}%", f"%{telefone_busca}%")
        )
        
        if df_busca.empty:
            st.warning("Nenhum registro encontrado para este número. Vá na aba 'Coletar' para puxar as informações das APIs e relatórios.")
        else:
            st.success(f"Encontrados {len(df_busca)} eventos consolidados para o número {telefone_busca}:")
            
            for idx, row in df_busca.iterrows():
                status_txt = str(row["status"])
                origem_fonte = str(row["origem_relatorio"])
                
                if "Atendida" in status_txt:
                    icone = "🟢 [ATENDIDA]"
                elif "Recusada" in status_txt or "Intercom" in status_txt:
                    icone = "🟠 [RECUSA / INTERCOM / P.A.]"
                else:
                    icone = "🔴 [EVENTO / ABANDONADA]"

                st.markdown(f"""
                ---
                #### {icone} — `{row['data_hora']}` (Fonte: `{origem_fonte.upper()}`)
                * **Cliente (Origem):** `{row['numero_origem']}`
                * **Destino / Atendente / Técnico:** `{row['tecnico'] or 'N/A'}` (Ramal: `{row['ramal_destino'] or 'N/A'}`)
                * **Status:** `{status_txt}` | **Duração:** `{row['duracao']}`
                * *Call ID:* `{row['call_id']}`
                """)
            
            st.markdown("---")
            st.subheader("Tabela Analítica Completa")
            st.dataframe(df_busca[[
                "data_hora", "origem_relatorio", "numero_origem", "tecnico",
                "ramal_destino", "status", "duracao", "call_id"
            ]])

with tab_mensal:
    st.subheader("Fechamento Mensal e Estatísticas por Técnico")
    mes_filtro = st.text_input("Mês (Ex: 2026-07)", "2026-07")
    
    query = "SELECT * FROM chamadas"
    params = ()
    if mes_filtro:
        query += " WHERE data_hora LIKE ?"
        params = (f"%{mes_filtro}%",)
        
    df_mes = pd.read_sql_query(query, conn, params=params)

    if df_mes.empty:
        st.warning("Nenhum dado encontrado para o período.")
    else:
        resumo = df_mes.groupby(["tecnico", "status"]).size().reset_index(name="quantidade")
        st.dataframe(resumo)

        fig = px.bar(
            resumo, x="tecnico", y="quantidade", color="status",
            title="Volume de Atendimentos e Ocorrências por Técnico", barmode="group"
        )
        st.plotly_chart(fig, use_container_width=True)

        csv = df_mes.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Baixar CSV Consolidado", csv, "auditoria_mensal.csv", "text/csv")

import streamlit as st
from html import escape as html_escape
import pandas as pd
import gspread
from datetime import datetime, timedelta
import time
import pytz
import os
import requests
import streamlit.components.v1 as components
from google import genai
from streamlit_autorefresh import st_autorefresh
from fpdf import FPDF
import unicodedata
import base64
import numpy as np
import re
import hashlib
import firebase_admin
from firebase_admin import credentials, firestore
from gspread.utils import rowcol_to_a1
from google.cloud.firestore_v1.base_query import FieldFilter
# =========================================================================
# 🔥 CONEXÃO BLINDADA COM O FIREBASE (À PROVA DE CACHE E FALHAS)
# =========================================================================
@st.cache_resource
def iniciar_banco_firebase():
    try:
        # Puxa a chave correta e conserta o texto caso o Streamlit o quebre
        creds_dict = {k: v for k, v in st.secrets["firebase"].items()}
        if "private_key" in creds_dict:
            creds_dict["private_key"] = creds_dict["private_key"].replace('\\n', '\n')
        
        # Inicia a aplicação se ainda não existir
        if not firebase_admin._apps:
            cred = credentials.Certificate(creds_dict)
            firebase_admin.initialize_app(cred)
            
        # Retorna o motor do banco de dados
        return firestore.client()
    except Exception as e:
        return f"ERRO: {str(e)}"

# Variável inteligente que mostra o erro na tela caso falhe
db_resultado = iniciar_banco_firebase()

if isinstance(db_resultado, str):
    st.error(f"🚨 Falha na conexão com o Firebase: {db_resultado}")
    db = None
else:
    db = db_resultado

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(page_title="Central de Operações | Frigelar", page_icon="❄️", layout="wide")

# --- 👑 ADMINISTRAÇÃO E SQUADS ---
ADMINS = ["Eduardo", "EduardoSouza", "Gestor", "Lopes", "eduardosouza", "biancamoura", "andreacastro"] 

SQUAD_AZIX = ["charleneoliveira", "brunasouza2", "viniciosmarques2"] 
SQUAD_MKTP = ["vitoriabraga", "fabiolapereira"] 
SQUAD_ATIVAS = ["Ruan Athaide", "Camila Garcia", "Marlise Borges", "Daiane Habowski", "Yasmine Goulart", "Raissa Silva", "Roger Santos", "Bianca Brasil", "Andressa Marchaki", "Viviane Santos", "Maria Elisa", "Endrio Silva", "Alex Alves", "Franscielle Leal", "Sophie Barbosa", "Bruna Tybusch","Ana Bezerra", "Franciele Silva", "Sarana Zatti", "Julia Mariane", "Angelica Bagatini", "Franciele Oliveira", "Vinicios Eduardo" ]

# --- META DIÁRIA E CELEBRAÇÃO ---
META_DIARIA = 50

# Classificação apenas na importação Qualitor; não recalcula o aging diariamente.
ETAPA_AGING_QUALITOR = "Em aberto > 20 dias úteis"


def preparar_importacao_qualitor(df_bruto):
    """Prepara a base em memória, sem consultar ou alterar planilhas/reservas."""
    obrigatorias = ['PROCESSO', 'Chamado', 'Aging abertura']
    ausentes = [coluna for coluna in obrigatorias if coluna not in df_bruto.columns]
    if ausentes:
        raise ValueError(
            "Importação Qualitor não realizada. Colunas ausentes: "
            + ", ".join(ausentes)
            + ". Recalcule e salve a planilha antes de enviar."
        )

    processo_bruto = df_bruto['PROCESSO'].fillna('').astype(str).str.strip()
    # Aceita o texto com hífen, como na base atual, e também a grafia antiga.
    ignorar = processo_bruto.str.contains(
        r"SOLICITANTE\s*[-–—]?\s*ATUALIZAR INFORMAÇÕES",
        case=False,
        na=False,
        regex=True,
    )
    df_filtrado = df_bruto.loc[~ignorar].copy()
    processo = processo_bruto.loc[df_filtrado.index]
    processo_normalizado = processo.str.replace(r'\s+', ' ', regex=True).str.upper()

    # O valor é o resultado salvo no Excel, não uma nova contagem de datas.
    aging = pd.to_numeric(
        df_filtrado['Aging abertura'].astype(str).str.strip().str.replace(',', '.', regex=False),
        errors='coerce',
    )
    aging_invalido = aging.isna() | ~np.isfinite(aging) | aging.lt(0)
    if aging_invalido.any():
        raise ValueError(
            f"Importação Qualitor não realizada: {int(aging_invalido.sum())} "
            "registro(s) com 'Aging abertura' vazio, inválido ou negativo. "
            "Recalcule e salve o Excel; nenhum chamado foi substituído."
        )

    de_para = {
        "(SAC) - ARREPENDIMENTO V3": "Arrependimento",
        "(SAC) - CANCELAMENTO V3": "Cancelamento de pedido",
        "(SAC) - ATRASO V3": "Atraso de Entrega",
        "(SAC) - PRODUTO ERRADO V3": "Produto Errado",
        "(SAC) - AVARIA V3": "Avaria",
        "(SAC) - ESTORNADOS": "Estornados",
        "(SAC) - EXTRAVIO V6": "Extravio",
    }
    motivo_original = processo_normalizado.map(de_para).fillna(processo)
    is_estornados = processo_normalizado.eq('(SAC) - ESTORNADOS')
    is_mktp = pd.Series(False, index=df_filtrado.index)
    if 'Etapa' in df_filtrado.columns:
        is_mktp = df_filtrado['Etapa'].astype(str).str.contains(
            r'REEMBOLSO\s+MKTP', case=False, na=False, regex=True
        )
    is_prio = pd.Series(False, index=df_filtrado.index)
    if 'Lista' in df_filtrado.columns:
        is_prio = df_filtrado['Lista'].astype(str).str.contains(
            'PRIORIDADE 1', case=False, na=False, regex=False
        )

    etapa = motivo_original.copy()
    protegidos = is_estornados | is_mktp
    etapa.loc[is_prio & ~protegidos] = 'Prioridade'
    etapa.loc[aging.gt(20) & ~protegidos] = ETAPA_AGING_QUALITOR
    # Mantém a precedência de Reembolso MKTP já existente no importador.
    etapa.loc[is_mktp] = 'Reembolso MKTP'
    sla = pd.Series('Normal ✅', index=df_filtrado.index)
    sla.loc[is_prio] = '🔥 Prioridade (Vence Hoje)'

    # Preserva as oito colunas atuais e acrescenta os metadados ao final.
    df_novo = pd.DataFrame({
        'ID': '',
        'Dados': df_filtrado['Chamado'].astype(str).str.replace(r'\.0$', '', regex=True),
        'Status': 'Pendente',
        'Etapa': etapa,
        'SLA': sla,
        'Responsavel': '',
        'Inicio': '',
        'Data_Conclusao': '',
        'Motivo_Original': motivo_original,
        'Aging_Abertura': aging,
    }, index=df_filtrado.index)
    df_pronto = df_novo.drop_duplicates(subset=['Dados'])
    na_nova_etapa = df_pronto['Etapa'].eq(ETAPA_AGING_QUALITOR)
    resumo = {
        'lidos': len(df_bruto),
        'ignorados_solicitante': int(ignorar.sum()),
        'duplicados': len(df_novo) - len(df_pronto),
        'importados': len(df_pronto),
        'aging_maior_20': int(na_nova_etapa.sum()),
        'aging_prioritarios': int((na_nova_etapa & df_pronto['SLA'].str.contains('Prioridade')).sum()),
        'estornados': int(df_pronto['Etapa'].eq('Estornados').sum()),
        'reembolso_mktp': int(df_pronto['Etapa'].eq('Reembolso MKTP').sum()),
    }
    return df_pronto, resumo

def verificar_meta_baloes(usuario_atual, df_ranking, meta):
    feitos_hoje = 0
    if not df_ranking.empty:
        minha_linha = df_ranking[df_ranking['Nome'] == usuario_atual]
        if not minha_linha.empty:
            feitos_hoje = int(minha_linha.iloc[0]['Qtd'])
    
    feitos_agora = feitos_hoje + 1
    if feitos_agora in [10, 25, meta, 75, 100]:
        st.session_state['soltar_baloes'] = True

# --- 🧠 CONFIGURAÇÃO DA IA (CARREGAMENTO SOMENTE SOB DEMANDA) ---
MODELO_GEMINI = "gemini-3.1-flash-lite"

@st.cache_resource
def iniciar_cliente_gemini():
    """Cria um único cliente por processo, apenas quando a IA for utilizada."""
    api_key = st.secrets.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY não configurada.")
    return genai.Client(api_key=api_key)

def gerar_conteudo_gemini(prompt):
    cliente = iniciar_cliente_gemini()
    return cliente.models.generate_content(
        model=MODELO_GEMINI,
        contents=prompt,
    )

ia_ativa = bool(st.secrets.get("GEMINI_API_KEY", ""))

# --- 🚨 ALERTA MICROSOFT TEAMS ---
def alertar_teams(mensagem):
    webhook_url = st.secrets.get("TEAMS_WEBHOOK_URL", "")
    if webhook_url:
        try:
            requests.post(webhook_url, json={"text": mensagem}, timeout=5)
        except Exception as e:
            print(f"Erro ao alertar Teams: {e}")

# --- CONEXÃO BLINDADA (ANTI-TRAVAMENTO) ---
@st.cache_resource
def conectar_e_abrir_abas():
    try:
        if os.path.exists("credentials.json"):
            client = gspread.service_account(filename="credentials.json")
        else:
            try:
                creds_dict = st.secrets["gcp_service_account"]
                client = gspread.service_account_from_dict(creds_dict)
            except:
                return "🚨 ERRO: Credenciais não encontradas.", None, None, None, None, None, None
        
        erro_real = ""
        for tentativa in range(3):
            try:
                sh = client.open("Chamados_Qualitor") 
                abas = sh.worksheets()
                if len(abas) >= 6:
                    aba_chamados = sh.worksheet("Chamados")
                    aba_users = sh.worksheet("Colaboradores")
                    aba_logs = sh.worksheet("Registros")
                    aba_transp = sh.worksheet("Transportadoras")
                    aba_azix = sh.worksheet("Azix")
                    aba_ativas = sh.worksheet("Ativas_Mktp") 
                    
                    return sh, aba_chamados, aba_users, aba_logs, aba_transp, aba_azix, aba_ativas
                else: erro_real = "A planilha tem menos de 2 abas visíveis."
            except Exception as e:
                erro_real = str(e)
                time.sleep(2 ** tentativa)
                
        return f"Falha após 3 tentativas. Erro: {erro_real}", None, None, None, None, None, None
    except Exception as e:
        return f"Erro Crítico: {str(e)}", None, None, None, None, None, None

# --- FUNÇÕES DE TEMPO E SLA ---
def hora_brasil():
    fuso = pytz.timezone('America/Sao_Paulo')
    return datetime.now(fuso) 

def hora_texto(): return hora_brasil().strftime("%d/%m/%Y %H:%M:%S")
def data_hoje(): return hora_brasil().strftime("%d/%m/%Y")

def calcular_duracao_str(inicio_str, fim_str):
    try:
        fmt = "%d/%m/%Y %H:%M:%S"
        inicio = datetime.strptime(str(inicio_str), fmt)
        fim = datetime.strptime(str(fim_str), fmt)
        minutos = int((fim - inicio).total_seconds() / 60)
        if minutos < 0: return "0m"
        horas, resto = divmod(minutos, 60)
        if horas > 0: return f"{horas}h {resto}m"
        return f"{resto}m"
    except: return "-"

def calcular_sla_bizdays(data_entrada_str):
    if pd.isna(data_entrada_str) or str(data_entrada_str).strip() == "" or str(data_entrada_str).lower() == "nan":
        return "⏳ A calcular..."
    try:
        data_str = str(data_entrada_str).split(" ")[0]
        data_entrada = datetime.strptime(data_str, "%d/%m/%Y").date()
        hoje = hora_brasil().date()
        
        dias_uteis = len(pd.bdate_range(start=data_entrada, end=hoje)) - 1
        if dias_uteis < 0: dias_uteis = 0
        
        if dias_uteis <= 2: return f"✅ No Prazo ({dias_uteis}/3 dias)"
        elif dias_uteis == 3: return f"🔥 Vence Hoje (3/3 dias)"
        else: return f"🚨 Atrasado ({dias_uteis} dias úteis)"
    except: return "⏳ A calcular..."

# --- INICIAR CONEXÃO ---
sh, aba_chamados, aba_users, aba_logs, aba_transp, aba_azix, aba_ativas = conectar_e_abrir_abas()
if isinstance(sh, str): st.error(f"Erro de conexão: {sh}"); st.stop()
if aba_chamados is None: st.error("Erro ao carregar abas principais."); st.stop()


# =========================================================================
# 🔥 REGISTRO DE LOGS NO FIREBASE (ZERO PESO NA MEMÓRIA)
# =========================================================================
def registrar_log(usuario, acao):
    try:
        if db is not None:
            # Usa set para forçar a criação com um ID baseado no tempo para garantir a ordem
            log_id = str(int(time.time() * 1000)) 
            db.collection('logs_qualitor').document(log_id).set({
                "Usuario": str(usuario),
                "Acao": str(acao),
                "DataHora": hora_texto(),
                "Data": data_hoje(),
                "CriadoEm": firestore.SERVER_TIMESTAMP,
            })
    except Exception as e: 
        # Trocamos o pass por um print no console do Streamlit para você ver o erro lá se falhar
        print(f"Erro na gravação do log: {e}")

@st.cache_data(ttl=180, max_entries=1, show_spinner=False)
def carregar_logs_dia():
    if db is None: 
        return pd.DataFrame(columns=["Usuario", "Acao", "DataHora"])
    try:
        # Mantém um teto de segurança. Os novos registros também recebem o campo Data.
        docs = (
            db.collection('logs_qualitor')
            .order_by("__name__", direction=firestore.Query.DESCENDING)
            .limit(3000)
            .stream()
        )
        lista_logs = [doc.to_dict() for doc in docs]
        
        if not lista_logs: 
            return pd.DataFrame(columns=["Usuario", "Acao", "DataHora"])
            
        df = pd.DataFrame(lista_logs)
        for col in ["Usuario", "Acao", "DataHora"]:
            if col not in df.columns: df[col] = ""
                
        df = df[["Usuario", "Acao", "DataHora"]]
        df = df.replace('', pd.NA).dropna(how='all').fillna('')
        return df
    except Exception as e:
        print(f"Erro na leitura dos logs: {e}")
        return pd.DataFrame(columns=["Usuario", "Acao", "DataHora"])

@st.cache_data(ttl=180, max_entries=1, show_spinner=False)
def carregar_metricas_dia():
    df_logs = carregar_logs_dia()
    ranking = pd.DataFrame(columns=["Nome", "Qtd"])
    metricas = {"azix_concluidos": 0, "azix_avancados": 0}
    if df_logs.empty:
        return ranking, metricas

    hoje = data_hoje()
    logs_hoje = df_logs[df_logs['DataHora'].astype(str).str.contains(hoje)]
    feitos = logs_hoje[logs_hoje['Acao'].astype(str).str.contains(
        "Finalizou|Encerrada|Concluiu Azix|Concluiu Ativa|Reivindicação Encerrada",
        case=False,
    )]
    if not feitos.empty:
        ranking = feitos['Usuario'].value_counts().reset_index()
        ranking.columns = ['Nome', 'Qtd']

    metricas["azix_concluidos"] = len(logs_hoje[
        logs_hoje['Acao'].astype(str).str.contains("Concluiu Azix", case=False)
    ])
    metricas["azix_avancados"] = len(logs_hoje[
        logs_hoje['Acao'].astype(str).str.contains("Azix para Mktp", case=False)
    ])
    return ranking, metricas
        
# =========================================================================
# 🔄 MOTORES DE DADOS (COM CADEADO DE MEMÓRIA - BLINDAGEM MÁXIMA)
# =========================================================================

def _processar_aba_leve(aba, nome_coluna_chave):
    """Função interna blindada para evitar estouro de RAM no Streamlit"""
    if aba is None: return pd.DataFrame()
    try:
        dados = aba.get_all_values()
            
        if not dados or len(dados) < 2: return pd.DataFrame()
        
        df = pd.DataFrame(dados[1:], columns=dados[0])
        df = df.loc[:, df.columns != '']
        df = df.replace('', pd.NA).dropna(how='all').fillna('')
        
        if nome_coluna_chave in df.columns: 
            df[nome_coluna_chave] = df[nome_coluna_chave].astype(str).str.replace(r'\.0$', '', regex=True)
            
        for col in ['Status', 'Etapa', 'SLA', 'Responsavel', 'Prioridade', 'Tipo_Atividade', 'Status_SLA']:
            if col in df.columns:
                df[col] = df[col].astype(str).str.strip().astype('category')
                
        return df
    except Exception as e:
        print(f"Erro ao carregar aba {aba.title}: {e}")
        return pd.DataFrame()

# Caches otimizados: TTL menor (60s) garante frescura, mas sem pesar.
@st.cache_data(ttl=180, max_entries=1, show_spinner=False)
def carregar_dados_chamados():
    return _processar_aba_leve(aba_chamados, 'Dados')

@st.cache_data(ttl=180, max_entries=1, show_spinner=False)
def carregar_dados_azix():
    return _processar_aba_leve(aba_azix, 'Nº Pedido venda')

@st.cache_data(ttl=180, max_entries=1, show_spinner=False)
def carregar_dados_ativas():
    return _processar_aba_leve(aba_ativas, 'Pedido')

@st.cache_data(ttl=120, max_entries=1, show_spinner=False)
def carregar_status_equipe():
    try:
        dados = aba_users.get_all_values()
            
        if not dados or len(dados) < 2: return pd.DataFrame()
        
        df = pd.DataFrame(dados[1:], columns=dados[0])
        return df.loc[:, df.columns != ''] 
    except: return pd.DataFrame()
        
@st.cache_data(ttl=3600, max_entries=1, show_spinner=False)
def carregar_agenda_transp():
    if aba_transp is None: return pd.DataFrame()
    try: return pd.DataFrame(aba_transp.get_all_records())
    except: return pd.DataFrame()

def invalidar_cache_base(nome_base):
    """Invalida apenas a base alterada, evitando recarregar todo o sistema."""
    if nome_base == "qualitor":
        carregar_dados_chamados.clear()
    elif nome_base == "azix":
        carregar_dados_azix.clear()
    elif nome_base == "ativas":
        carregar_dados_ativas.clear()
    elif nome_base == "equipe":
        carregar_status_equipe.clear()

def invalidar_cache_usuario(usuario_atual):
    if usuario_atual in SQUAD_AZIX or usuario_atual in SQUAD_MKTP:
        invalidar_cache_base("azix")
    elif usuario_atual in SQUAD_ATIVAS:
        invalidar_cache_base("ativas")
    else:
        invalidar_cache_base("qualitor")

def invalidar_todos_os_dados():
    carregar_dados_chamados.clear()
    carregar_dados_azix.clear()
    carregar_dados_ativas.clear()
    carregar_status_equipe.clear()
    carregar_logs_dia.clear()
    carregar_metricas_dia.clear()

def _reserva_ref(aba, linha):
    chave = f"{aba.id}:{int(linha)}"
    doc_id = hashlib.sha256(chave.encode("utf-8")).hexdigest()
    return db.collection("reservas_atendimento").document(doc_id)

@firestore.transactional
def _reservar_em_transacao(transaction, referencia, usuario, aba_id, linha):
    snapshot = referencia.get(transaction=transaction)
    if snapshot.exists:
        dados_reserva = snapshot.to_dict() or {}
        dono = str(dados_reserva.get("Usuario", ""))
        if dono and dono != usuario:
            return False, dono

    transaction.set(referencia, {
        "Usuario": usuario,
        "AbaId": str(aba_id),
        "Linha": int(linha),
        "Status": "Em Andamento",
        "AtualizadoEm": firestore.SERVER_TIMESTAMP,
    })
    return True, usuario

def reservar_atendimento(aba, linha, usuario, status_destino, col_status, col_resp, col_inicio):
    """Reserva no Firestore e grava as três células do Sheets em uma única chamada."""
    if db is None:
        return False, "Firebase indisponível; a reserva segura não foi realizada."

    referencia = _reserva_ref(aba, linha)
    transacao = db.transaction()
    reservado, dono = _reservar_em_transacao(
        transacao, referencia, usuario, aba.id, linha
    )
    if not reservado:
        return False, f"Este atendimento acabou de ser reservado por {dono}. Atualize a fila."

    try:
        aba.batch_update([
            {"range": rowcol_to_a1(linha, col_status), "values": [[status_destino]]},
            {"range": rowcol_to_a1(linha, col_resp), "values": [[usuario]]},
            {"range": rowcol_to_a1(linha, col_inicio), "values": [[hora_texto()]]},
        ])
        return True, "Atendimento reservado."
    except Exception:
        referencia.delete()
        raise

def liberar_reserva(aba, linha):
    if db is None or aba is None:
        return
    try:
        _reserva_ref(aba, linha).delete()
    except Exception as e:
        print(f"Erro ao liberar reserva: {e}")

def limpar_reservas_aba(aba):
    """Remove reservas antigas quando uma aba inteira é regravada e as linhas mudam."""
    if db is None or aba is None:
        return
    try:
        docs = db.collection("reservas_atendimento").where(
            filter=FieldFilter("AbaId", "==", str(aba.id))
        ).stream()
        lote = db.batch()
        quantidade = 0
        for doc in docs:
            lote.delete(doc.reference)
            quantidade += 1
            if quantidade % 400 == 0:
                lote.commit()
                lote = db.batch()
        if quantidade % 400:
            lote.commit()
    except Exception as e:
        print(f"Erro ao limpar reservas da aba: {e}")

def ler_mural():
    try:
        with open("mural.txt", "r", encoding="utf-8") as f: return f.read().strip()
    except: return ""

def salvar_mural(texto):
    with open("mural.txt", "w", encoding="utf-8") as f: f.write(texto)

def is_marketplace(texto):
    palavras_chave = ['MAGAZINE', 'MERCADO', 'B2W', 'AMAZON', 'SHOPEE', 'CARREFOUR']
    return any(k in str(texto).upper() for k in palavras_chave)

if 'soltar_baloes' in st.session_state and st.session_state['soltar_baloes']:
    st.balloons(); st.session_state['soltar_baloes'] = False

if 'tema_escolhido' not in st.session_state: st.session_state['tema_escolhido'] = "Padrão"

# --- IDENTIDADE VISUAL: somente apresentação, sem leituras externas ---
def aplicar_visual_frigelar(tema="Padrão"):
    paletas = {
        "Padrão": ("#f3f6fb", "#ffffff", "#172d49", "#576b82", "#dce5ef", "#075bb6", "#eaf2fc"),
        "Escuro": ("#0f1927", "#19293c", "#edf3fb", "#b5c4d7", "#34465d", "#3281df", "#243d5d"),
        "Matrix": ("#0a1710", "#10281b", "#d8f5df", "#add2b9", "#315842", "#168347", "#19442b"),
        "Rosa": ("#fff4f8", "#ffffff", "#51263c", "#825c70", "#ecd6e1", "#b22f70", "#fce4ef"),
    }
    fundo, painel, texto, suave, borda, azul, destaque = paletas.get(tema, paletas["Padrão"])
    css = """
    <style>
    :root { --frg-bg:__BG__; --frg-panel:__PANEL__; --frg-text:__TEXT__;
      --frg-muted:__MUTED__; --frg-border:__BORDER__; --frg-primary:__PRIMARY__; --frg-soft:__SOFT__; }
    .stApp { background:var(--frg-bg); color:var(--frg-text); }
    [data-testid="stHeader"] { background:var(--frg-bg); }
    [data-testid="stMainBlockContainer"], .main .block-container {
      max-width:1320px; padding-top:2.4rem; padding-bottom:2rem; }
    [data-testid="stMain"] h1, [data-testid="stMain"] h2, [data-testid="stMain"] h3,
    [data-testid="stMain"] label { color:var(--frg-text); }
    [data-testid="stMain"] h1 { font-size:2rem; letter-spacing:-.04em; }
    [data-testid="stMain"] h2 { font-size:1.4rem; letter-spacing:-.02em; }
    [data-testid="stMain"] h3 { font-size:1.15rem; }
    [data-testid="stCaptionContainer"] { color:var(--frg-muted); }
    [data-testid="stMain"] hr { margin:.75rem 0; border-color:var(--frg-border); }
    [data-testid="stMain"] [data-testid="stForm"],
    [data-testid="stMain"] [data-testid="stExpander"] {
      background:var(--frg-panel); border-color:var(--frg-border); border-radius:12px; }
    [data-testid="stMain"] [data-testid="stMetric"] {
      background:var(--frg-panel); border:1px solid var(--frg-border); border-radius:12px; padding:1rem; }
    [data-testid="stMain"] [data-testid="stMetricValue"] { color:var(--frg-text); }
    [data-testid="stMain"] [data-testid="stButton"] button,
    [data-testid="stMain"] [data-testid="stFormSubmitButton"] button,
    [data-testid="stMain"] [data-testid="stDownloadButton"] button {
      min-height:2.7rem; border-radius:8px; font-weight:600; }
    [data-testid="stMain"] button[kind="primary"] { background:var(--frg-primary); color:white; border-color:var(--frg-primary); }
    [data-testid="stMain"] button[kind="secondary"] {
      background:var(--frg-panel); color:var(--frg-text); border-color:var(--frg-border); }
    [data-testid="stMain"] input, [data-testid="stMain"] textarea {
      color:var(--frg-text); background:var(--frg-panel); }
    [data-testid="stMain"] [data-baseweb="input"],
    [data-testid="stMain"] [data-baseweb="base-input"],
    [data-testid="stMain"] [data-baseweb="select"] > div,
    [data-testid="stMain"] [data-baseweb="textarea"] {
      color:var(--frg-text); background:var(--frg-panel); border-color:var(--frg-border); }
    [data-testid="stSidebar"] { background:#073a72; color:#f1f6fd; }
    [data-testid="stSidebar"] [data-testid="stSidebarContent"] { padding-top:1rem; }
    [data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3,
    [data-testid="stSidebar"] label { color:#f1f6fd; }
    [data-testid="stSidebar"] [data-testid="stCaptionContainer"] { color:#c7d8ed; }
    [data-testid="stSidebar"] hr { border-color:#355c89; margin:.75rem 0; }
    [data-testid="stSidebar"] [data-testid="stExpander"] { border-color:#426993; }
    [data-testid="stSidebar"] [data-testid="stButton"] button {
      border-color:#456c98; background:#12497f; color:#f1f6fd; border-radius:7px; }
    [data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] {
      background:#eaf3ff; color:#073a72; border-color:#eaf3ff; }
    .st-key-frg-navigation [role="radiogroup"] { gap:.3rem; }
    .st-key-frg-navigation label[data-baseweb="radio"] {
      width:100%; margin:0; padding:.65rem .7rem; border-radius:8px; }
    .st-key-frg-navigation label[data-baseweb="radio"]:has(input:checked) { background:#fff; color:#073a72; }
    .st-key-frg-navigation label[data-baseweb="radio"]:has(input:checked) p { color:#073a72; font-weight:600; }
    .st-key-frg-navigation label[data-baseweb="radio"] > div:first-child { display:none; }
    .frg-brand { padding:.2rem 0 .9rem; }
    .frg-brand strong { color:#fff; font-size:1.85rem; letter-spacing:-.07em; }
    .frg-brand p { color:#c7d8ed; margin:.1rem 0 0; font-size:.82rem; }
    .frg-user { margin:.75rem 0; color:#fff; overflow-wrap:anywhere; }
    .frg-user span { display:block; color:#c7d8ed; font-size:.8rem; margin-top:.15rem; }
    .frg-sidebar-status { padding:.65rem .8rem; background:#12497f; border-radius:8px; margin:.3rem 0 .75rem; color:#f1f6fd; }
    .frg-hero { display:flex; gap:1rem; justify-content:space-between; align-items:center; margin:0 0 1.25rem; flex-wrap:wrap; }
    .frg-hero h1 { margin:0; padding:0; font-size:2rem; color:var(--frg-text); line-height:1.2; }
    .frg-hero p { margin:.5rem 0 0; color:var(--frg-muted); font-size:.95rem; }
    .frg-eyebrow { text-transform:uppercase; letter-spacing:.12em; font-size:.7rem; color:var(--frg-muted); margin-bottom:.5rem; }
    .frg-status { display:inline-block; background:var(--frg-soft); color:var(--frg-text); border-radius:18px; padding:.45rem .85rem; font-size:.85rem; }
    .frg-stat { background:var(--frg-panel); border:1px solid var(--frg-border); border-radius:12px; padding:1.2rem; }
    .frg-stat-label { color:var(--frg-muted); font-size:.9rem; margin-bottom:.45rem; }
    .frg-stat-value { font-size:2.2rem; font-weight:650; color:var(--frg-text); line-height:1.25; font-variant-numeric:tabular-nums; }
    .frg-stat-note { color:var(--frg-muted); font-size:.85rem; margin-top:.55rem; overflow-wrap:anywhere; }
    .frg-stat-inline { border:0; padding:0; background:transparent; }
    .frg-progress { height:8px; background:var(--frg-border); border-radius:8px; overflow:hidden; margin:.9rem 0 .6rem; }
    .frg-progress-fill { height:100%; background:var(--frg-primary); }
    .frg-progress-note { font-size:.82rem; color:var(--frg-muted); }
    [class*="st-key-frg-queue"], [class*="st-key-frg-goal"], [class*="st-key-frg-details"],
    .st-key-frg-import { background:var(--frg-panel); border-radius:12px; }
    .frg-login-brand { text-align:center; margin-bottom:1rem; }
    .frg-login-brand img { max-width:185px; max-height:75px; object-fit:contain; }
    .frg-login-brand strong { display:block; font-size:2rem; letter-spacing:-.05em; color:#075bb6; }
    .frg-login-brand h1 { font-size:1.7rem!important; color:#172d49!important; margin-top:1rem; }
    .frg-login-brand p { color:#576b82; font-size:.95rem; }
    @media(max-width:768px) {
      [data-testid="stMainBlockContainer"], .main .block-container { padding:1.5rem 1rem; }
      .frg-hero h1 { font-size:1.65rem; }
      .frg-stat-value { font-size:1.85rem; }
    }
    </style>
    """
    for marcador, valor in zip(
        ["__BG__", "__PANEL__", "__TEXT__", "__MUTED__", "__BORDER__", "__PRIMARY__", "__SOFT__"],
        [fundo, painel, texto, suave, borda, azul, destaque],
    ):
        css = css.replace(marcador, valor)
    st.markdown(css, unsafe_allow_html=True)


def renderizar_cabecalho(titulo, descricao, operacao, status):
    status_texto = "Disponível" if status == "Disponivel" else str(status)
    st.markdown(
        '<div class="frg-hero"><div><div class="frg-eyebrow">Central de operações · '
        + html_escape(str(operacao)) + '</div><h1>' + html_escape(str(titulo))
        + '</h1><p>' + html_escape(str(descricao)) + '</p></div><span class="frg-status">'
        + html_escape(status_texto) + '</span></div>', unsafe_allow_html=True,
    )


def renderizar_indicador(rotulo, valor, detalhe="", compacto=False):
    classe = "frg-stat frg-stat-inline" if compacto else "frg-stat"
    st.markdown(
        f'<div class="{classe}"><div class="frg-stat-label">{html_escape(str(rotulo))}</div>'
        f'<div class="frg-stat-value">{html_escape(str(valor))}</div>'
        f'<div class="frg-stat-note">{html_escape(str(detalhe))}</div></div>',
        unsafe_allow_html=True,
    )


def renderizar_meta(qtd, meta):
    qtd = int(qtd)
    percentual = max(0.0, min(qtd / meta, 1.0)) if meta else 0.0
    renderizar_indicador("Seu desempenho hoje", f"{qtd} / {meta}", "Chamados concluídos hoje", compacto=True)
    st.markdown(
        f'<div class="frg-progress" role="progressbar" aria-label="Meta diária" '
        f'aria-valuemin="0" aria-valuemax="{meta}" aria-valuenow="{min(qtd, meta)}">'
        f'<div class="frg-progress-fill" style="width:{percentual * 100:.1f}%"></div></div>'
        f'<div class="frg-progress-note">{percentual:.0%} do objetivo</div>', unsafe_allow_html=True,
    )


aplicar_visual_frigelar(st.session_state["tema_escolhido"])

# ===================================================
# 🎫 TELA DE LOGIN CORPORATIVA BLINDADA
# ===================================================
def render_corporate_login():
    st.markdown("""
    <style>
      .stApp { background:#edf3fb; }
      [data-testid="stSidebar"] { display:none; }
      [data-testid="stMainBlockContainer"] { padding-top:5rem; max-width:1100px; }
      [data-testid="stForm"] { background:#fff!important; padding:2rem!important;
        border:1px solid #dce5ef!important; border-radius:16px!important; }
      [data-testid="stForm"] input { background:#fff!important; color:#172d49!important; }
      [data-testid="stForm"] label { color:#172d49!important; }
      [data-testid="stForm"] button[kind="primary"] { background:#075bb6!important; color:#fff!important; }
    </style>
    """, unsafe_allow_html=True)

if 'usuario' not in st.session_state:
    df_equipe = carregar_status_equipe()
    if not df_equipe.empty and 'Colaboradores' in df_equipe.columns:
        lista_nomes = [n for n in df_equipe['Colaboradores'].tolist() if str(n).strip() != '']
        senhas = dict(zip(df_equipe['Colaboradores'], df_equipe.get('Senha', ['']*len(df_equipe))))
    else:
        lista_nomes = []; senhas = {}
        st.warning("⚠️ Planilha a carregar. Aguarde.")
        if st.button("🔄 Recarregar Nomes"): invalidar_cache_base("equipe"); st.rerun()

    render_corporate_login()
    def get_image_base64(caminho_imagem):
        try:
            with open(caminho_imagem, "rb") as img_file: return base64.b64encode(img_file.read()).decode()
        except: return "" 
    logo_b64 = get_image_base64("logo_frigelar.png")
    
    c1, c2, c3 = st.columns([1, 2, 1]) 
    with c2:
        with st.form("login_form", clear_on_submit=False):
            marca_login = (
                f'<img src="data:image/png;base64,{logo_b64}" alt="Frigelar">'
                if logo_b64 else '<strong>frigelar</strong>'
            )
            st.markdown(
                '<div class="frg-login-brand">' + marca_login
                + '<h1>Bem-vindo</h1><p>Acesse sua central de atendimento.</p></div>',
                unsafe_allow_html=True,
            )
            user_digitado = st.text_input("Utilizador", placeholder="Coloque o seu usuário", label_visibility="collapsed")
            senha_digitada = st.text_input("Senha", type="password", placeholder="Coloque a sua senha", label_visibility="collapsed")
            submitted = st.form_submit_button("Entrar", type="primary", width="stretch")
            if submitted:
                if user_digitado in lista_nomes and str(senha_digitada) == str(senhas.get(user_digitado, "")):
                    st.session_state['usuario'] = user_digitado
                    st.session_state['tamanho_fila_anterior'] = 0 
                    registrar_log(user_digitado, "LOGIN") 
                    try:
                        idx = df_equipe.index[df_equipe['Colaboradores'] == user_digitado].tolist()[0] + 2
                        aba_users.update_cell(idx, 3, "Disponivel")
                        invalidar_cache_base("equipe")
                    except: pass
                    st.rerun()
                else: st.error("❌ Login não encontrado ou senha incorreta.")

# ===================================================
# SISTEMA LOGADO
# ===================================================
else:
    usuario = st.session_state['usuario']
    df_equipe = carregar_status_equipe()

    # Operadores recebem somente métricas resumidas, não milhares de logs brutos.
    ranking_global, metricas_hoje = carregar_metricas_dia()

    hora_atual = hora_brasil().hour
    if 6 <= hora_atual < 12: saudacao = "Bom dia"; sub_saudacao = "Pronto para os chamados?"
    elif 12 <= hora_atual < 18: saudacao = "Boa tarde"; sub_saudacao = "Como está a esteira?"
    else: saudacao = "Boa noite"; sub_saudacao = "Quase na hora de descansar!"
    
    status_real = "Erro"
    linha_planilha = None
    minhas_etapas = ['Todas']
    
    if not df_equipe.empty and 'Colaboradores' in df_equipe.columns:
        meus_dados = df_equipe[df_equipe['Colaboradores'] == usuario]
        if not meus_dados.empty:
            status_real = meus_dados.iloc[0].get('Status', 'Erro')
            etapas_str = str(meus_dados.iloc[0].get('Etapas_Permitidas', 'Todas'))
            minhas_etapas = [e.strip() for e in etapas_str.split(',')]
            idx = df_equipe.index[df_equipe['Colaboradores'] == usuario].tolist()[0]
            linha_planilha = idx + 2

    qtd_minha = 0
    if not ranking_global.empty:
        minha_posicao = ranking_global.index[ranking_global['Nome'] == usuario].tolist()
        if minha_posicao:
            qtd_minha = ranking_global.iloc[minha_posicao[0]]['Qtd']

    operacao_visual = (
        "Azix · Tratativas" if usuario in SQUAD_AZIX else
        "MKTP · Reivindicações" if usuario in SQUAD_MKTP else
        "Ativas Marketplace" if usuario in SQUAD_ATIVAS else "Qualitor"
    )
    # Navegação nativa: renderiza somente a área selecionada.
    modo_gerente = False
    secao_gestao = "Atendimento"
    with st.sidebar:
        st.markdown('<div class="frg-brand"><strong>frigelar</strong><p>Central de operações</p></div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="frg-user">' + html_escape(str(usuario)) + '<span>'
            + ("Acesso de gestor" if usuario in ADMINS else html_escape(operacao_visual))
            + '</span></div>', unsafe_allow_html=True,
        )
        if usuario in ADMINS:
            with st.container(key="frg-navigation"):
                secao_gestao = st.radio(
                    "Navegação", ["Atendimento", "Painel de gestão", "Importar bases", "Exportações", "Manutenção"],
                    key="frg_secao_gestao", label_visibility="collapsed",
                )
            modo_gerente = secao_gestao != "Atendimento"
        else:
            st.caption("ÁREA DE ATENDIMENTO · " + operacao_visual)
        st.divider()
        status_visual = "Disponível" if status_real == "Disponivel" else str(status_real)
        st.markdown('<div class="frg-sidebar-status">Status: <b>' + html_escape(status_visual) + '</b></div>', unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        if c1.button("🟢 Online", type="primary" if status_real == "Disponivel" else "secondary", width="stretch"):
            if linha_planilha: aba_users.update_cell(linha_planilha, 3, "Disponivel"); registrar_log(usuario, "Ficou Disponivel"); invalidar_cache_base("equipe"); st.rerun()
        if c2.button("☕ Pausa", type="primary" if status_real == "Pausa" else "secondary", width="stretch"):
            if linha_planilha: aba_users.update_cell(linha_planilha, 3, "Pausa"); registrar_log(usuario, "Entrou em Pausa"); invalidar_cache_base("equipe"); st.rerun()
        if st.button("🚽 Banheiro", width="stretch"):
            if linha_planilha: aba_users.update_cell(linha_planilha, 3, "Banheiro"); registrar_log(usuario, "Foi ao Banheiro"); invalidar_cache_base("equipe"); st.rerun()
        
        st.divider()
        st.caption("SEU PROGRESSO HOJE")
        st.progress(min(max(float(qtd_minha) / META_DIARIA, 0.0), 1.0))
        st.caption(f"{int(qtd_minha)} de {META_DIARIA} chamados concluídos")
        if not ranking_global.empty and minha_posicao:
            st.caption(f"Sua posição na equipe: {minha_posicao[0] + 1}º lugar")
        st.divider()
        with st.expander("⚙️ Meu Perfil"):
            if 'tema_escolhido' not in st.session_state: st.session_state['tema_escolhido'] = "Padrão"
            novo_tema = st.selectbox("🎨 Tema Visual", ["Padrão", "Hacker Matrix", "Dark Night", "Rosa Fofo"], index=["Padrão", "Hacker Matrix", "Dark Night", "Rosa Fofo"].index(st.session_state['tema_escolhido']))
            if novo_tema != st.session_state['tema_escolhido']:
                st.session_state['tema_escolhido'] = novo_tema
                st.rerun()
            st.divider()
            st.markdown("**Mudar Senha**")
            nova_senha = st.text_input("Digite a nova senha:", type="password")
            confirma_senha = st.text_input("Confirme a nova senha:", type="password")
            if st.button("Salvar Nova Senha", width="stretch"):
                if nova_senha == confirma_senha and len(nova_senha) >= 4:
                    try:
                        cols_users = df_equipe.columns.tolist()
                        if "Senha" in cols_users:
                            col_senha_idx = cols_users.index("Senha") + 1
                            aba_users.update_cell(linha_planilha, col_senha_idx, nova_senha)
                            registrar_log(usuario, "Mudou a própria senha")
                            st.success("✅ Senha atualizada! Use no próximo login.")
                            invalidar_cache_base("equipe")
                        else: st.error("Erro: Coluna 'Senha' não encontrada.")
                    except Exception as e: st.error(f"Erro ao mudar senha: {e}")
                else: st.warning("⚠️ Senhas não batem ou são curtas (mín. 4).")
                
        st.divider()
        if st.button("Sair (Logout)", width="stretch"): registrar_log(usuario, "LOGOUT"); del st.session_state['usuario']; st.rerun()

    # Carrega somente as bases necessárias para a tela atual.
    df_qualitor = pd.DataFrame()
    df_azix_data = pd.DataFrame()
    df_ativas_data = pd.DataFrame()

    if usuario == "TV" or modo_gerente:
        df_qualitor = carregar_dados_chamados()
        df_azix_data = carregar_dados_azix()
        df_ativas_data = carregar_dados_ativas()
    elif usuario in SQUAD_AZIX or usuario in SQUAD_MKTP:
        df_azix_data = carregar_dados_azix()
    elif usuario in SQUAD_ATIVAS:
        df_ativas_data = carregar_dados_ativas()
    else:
        df_qualitor = carregar_dados_chamados()

    if usuario in SQUAD_AZIX or usuario in SQUAD_MKTP:
        df = df_azix_data
        aba_atual = aba_azix
    elif usuario in SQUAD_ATIVAS:
        df = df_ativas_data
        aba_atual = aba_ativas
    else:
        df = df_qualitor
        aba_atual = aba_chamados

    if not df.empty:
        cols_planilha = df.columns.tolist()
        COL_STATUS = cols_planilha.index("Status") + 1 if "Status" in cols_planilha else 3
        COL_RESP = cols_planilha.index("Responsavel") + 1 if "Responsavel" in cols_planilha else 5
        COL_INICIO = cols_planilha.index("Inicio") + 1 if "Inicio" in cols_planilha else 6
        COL_FIM = cols_planilha.index("Data_Conclusao") + 1 if "Data_Conclusao" in cols_planilha else 7
    else:
        COL_STATUS, COL_RESP, COL_INICIO, COL_FIM = 3, 5, 6, 7

    if usuario != "TV":
        titulo_visual = secao_gestao if modo_gerente else "Minha fila"
        descricao_visual = (
            "Acompanhe os resultados e organize a operação." if modo_gerente else
            f"{saudacao}, {usuario}. {sub_saudacao}"
        )
        renderizar_cabecalho(titulo_visual, descricao_visual, "Gestão" if modo_gerente else operacao_visual, status_real)

    # ===================================================
    # 📺 MODO TELÃO (TV DO SALÃO)
    # ===================================================
    if usuario == "TV":
        st.markdown("<h1 style='text-align: center; color: #1E90FF; font-size: 50px;'>📺 Painel de Operações - Frigelar</h1>", unsafe_allow_html=True)
        st.markdown("""<style>[data-testid="stSidebar"], header {display: none;} .block-container {padding-top: 1rem; max-width: 98%;}</style>""", unsafe_allow_html=True)
        if st.button("Sair (Logout da TV)"): del st.session_state['usuario']; st.rerun()
        
        tot_q = len(df_qualitor)
        pend_q = len(df_qualitor[df_qualitor['Status'] == 'Pendente']) if not df_qualitor.empty else 0
        fora_sla_q = len(df_qualitor[(df_qualitor['Status'] == 'Pendente') & (df_qualitor['SLA'].astype(str).str.contains('Prioridade 1|fora', case=False, na=False))]) if not df_qualitor.empty and 'SLA' in df_qualitor.columns else 0
        dentro_sla_q = pend_q - fora_sla_q
        
        tot_a = len(df_azix_data)
        pend_a = len(df_azix_data[df_azix_data['Status'].isin(['Pendente', 'Pendente - Retorno', 'Aguardando Reivindicação'])]) if not df_azix_data.empty else 0
        fora_sla_a = len(df_azix_data[(df_azix_data['Status'].isin(['Pendente', 'Pendente - Retorno', 'Aguardando Reivindicação'])) & (df_azix_data['Status_SLA'].astype(str).str.contains('Atrasado|Vence Hoje', case=False, na=False))]) if not df_azix_data.empty and 'Status_SLA' in df_azix_data.columns else 0
        dentro_sla_a = pend_a - fora_sla_a
        
        # --- LÓGICA DE PRODUÇÃO AZIX HOJE (LIDA DOS LOGS) ---
        azix_hoje_conc = metricas_hoje["azix_concluidos"]
        azix_hoje_avan = metricas_hoje["azix_avancados"]
        
        colQ, colA = st.columns(2)
        with colQ:
            st.markdown("<div style='background-color: #f0f8ff; padding: 20px; border-radius: 10px; border-left: 8px solid #0284c7; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>", unsafe_allow_html=True)
            st.markdown("<h2 style='text-align: center; color: #0284c7; margin-top:0;'>🔷 OPERAÇÃO QUALITOR</h2>", unsafe_allow_html=True)
            st.markdown(f"<h3 style='text-align: center;'>📦 Total na Base: <b>{tot_q}</b></h3>", unsafe_allow_html=True)
            c1, c2, c3 = st.columns(3)
            c1.metric("🎫 Fila (Pendente)", pend_q)
            c2.metric("✅ No Prazo", dentro_sla_q)
            c3.metric("🔥 Atrasados/Críticos", fora_sla_q)
            st.markdown("</div>", unsafe_allow_html=True)
            
        with colA:
            st.markdown("<div style='background-color: #fffbeb; padding: 20px; border-radius: 10px; border-left: 8px solid #d97706; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>", unsafe_allow_html=True)
            st.markdown("<h2 style='text-align: center; color: #d97706; margin-top:0;'>🔶 OPERAÇÃO AZIX / MKTP</h2>", unsafe_allow_html=True)
            st.markdown(f"<h3 style='text-align: center;'>📦 Total na Base: <b>{tot_a}</b></h3>", unsafe_allow_html=True)
            c4, c5, c6 = st.columns(3)
            c4.metric("🎫 Fila Atual", pend_a)
            c5.metric("✅ No Prazo", dentro_sla_a)
            c6.metric("🔥 Atrasados", fora_sla_a)
            st.write("---")
            st.markdown("<p style='text-align: center; color: #92400e; margin-bottom:5px;'><b>📊 Desempenho Azix (Hoje):</b></p>", unsafe_allow_html=True)
            c7, c8 = st.columns(2)
            c7.metric("✅ Encerrados", azix_hoje_conc)
            c8.metric("⏭️ Passaram Etapa", azix_hoje_avan)
            st.markdown("</div>", unsafe_allow_html=True)

        st.write("---")
        st.markdown("<h3 style='text-align: center;'>🏆 DESTAQUES DO DIA (Equipe Geral)</h3>", unsafe_allow_html=True)
        if not ranking_global.empty:
            cols_rank = st.columns(min(3, len(ranking_global)))
            for i, row in ranking_global.head(3).iterrows():
                medalha = "🥇" if i == 0 else "🥈" if i == 1 else "🥉"
                cols_rank[i].markdown(f"""
                <div style='background-color: white; padding: 15px; border-radius: 10px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border: 1px solid #e2e8f0;'>
                    <h2 style='margin:0;'>{medalha} {i+1}º Lugar</h2>
                    <h3 style='color: #475569; margin: 10px 0;'>{row['Nome']}</h3>
                    <h1 style='color: #10b981; margin:0;'>{row['Qtd']} <span style='font-size: 0.4em; color: gray;'>Concluídos</span></h1>
                </div>
                """, unsafe_allow_html=True)
        
        texto_mural = ler_mural()
        if texto_mural: 
            st.write("---")
            st.warning(f"📢 **AVISO DA GESTÃO:** {texto_mural}")
            
        st_autorefresh(interval=60_000, key="refresh_tv")

    # ===================================================
    # 👑 VISÃO DO GESTOR (ADMIN)
    # ===================================================
    elif modo_gerente:
        st.caption(f"Última atualização: {hora_texto()}")
        if st.button("🔄 Atualizar Tudo"): invalidar_todos_os_dados(); st.rerun()

        if secao_gestao in ["Painel de gestão", "Exportações"]:
            # --- MÁQUINA DO TEMPO (FILTRO DE PERÍODO NO FORMATO BR) ---
            st.write("---")
            st.subheader("Período dos resultados")
            c_dt1, c_dt2 = st.columns(2)
            hoje_date = hora_brasil().date()
            data_inicio = c_dt1.date_input("Data Inicial", hoje_date - timedelta(days=7), format="DD/MM/YYYY", key="frg_data_inicio")
            data_fim = c_dt2.date_input("Data Final", hoje_date, format="DD/MM/YYYY", key="frg_data_fim")
        
            df_logs = carregar_logs_dia()
            ranking_periodo = pd.DataFrame()
            df_logs_periodo = pd.DataFrame()
        
            azix_concluidos_periodo = 0
            azix_avancados_periodo = 0
            azix_devolvidos_periodo = 0
            ativas_concluidos_periodo = 0
            importados_azix = 0

            if not df_logs.empty:
                df_logs['DataReal'] = pd.to_datetime(df_logs['DataHora'].str.split(' ').str[0], format="%d/%m/%Y", errors='coerce').dt.date
                df_logs_periodo = df_logs[(df_logs['DataReal'] >= data_inicio) & (df_logs['DataReal'] <= data_fim)].copy()
            
                feitos_periodo = df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Finalizou|Encerrada|Concluiu Azix|Concluiu Ativa", case=False)]
                if not feitos_periodo.empty:
                    ranking_periodo = feitos_periodo['Usuario'].value_counts().reset_index()
                    ranking_periodo.columns = ['Nome', 'Qtd']
                
                # Ler a história nos Logs
                azix_concluidos_periodo = len(df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Concluiu Azix", case=False)])
                azix_avancados_periodo = len(df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Azix para Mktp", case=False)])
                azix_devolvidos_periodo = len(df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Devolveu Azix", case=False)])
                ativas_concluidos_periodo = len(df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Concluiu Ativa", case=False)])
            
                # Soma os importados no período
                logs_importacao = df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Adicionou", case=False)]
                for acao in logs_importacao['Acao']:
                    nums = re.findall(r'\d+', str(acao))
                    if nums: importados_azix += int(nums[0])

            pend_q = len(df_qualitor[df_qualitor['Status'] == 'Pendente']) if not df_qualitor.empty else 0
            fora_sla_q = len(df_qualitor[(df_qualitor['Status'] == 'Pendente') & (df_qualitor['SLA'].astype(str).str.contains('Prioridade 1|fora', case=False, na=False))]) if not df_qualitor.empty and 'SLA' in df_qualitor.columns else 0
        
            pend_a = len(df_azix_data[df_azix_data['Status'].isin(['Pendente', 'Pendente - Retorno', 'Aguardando Reivindicação'])]) if not df_azix_data.empty else 0
            fora_sla_a = len(df_azix_data[(df_azix_data['Status'].isin(['Pendente', 'Pendente - Retorno', 'Aguardando Reivindicação'])) & (df_azix_data['Status_SLA'].astype(str).str.contains('Atrasado|Vence Hoje', case=False, na=False))]) if not df_azix_data.empty and 'Status_SLA' in df_azix_data.columns else 0
        
            pend_ativas = len(df_ativas_data[df_ativas_data['Status'] == 'Pendente']) if not df_ativas_data.empty else 0
            prio1_ativas = len(df_ativas_data[(df_ativas_data['Status'] == 'Pendente') & (df_ativas_data['Prioridade'].astype(str) == '1')]) if not df_ativas_data.empty and 'Prioridade' in df_ativas_data.columns else 0

            total_feitos_periodo = ranking_periodo['Qtd'].sum() if not ranking_periodo.empty else 0

        if secao_gestao == "Painel de gestão":
            st.subheader("Filas e produtividade")
            cards_gestao = st.columns(4)
            with cards_gestao[0]:
                renderizar_indicador("Qualitor · Pendentes", pend_q, f"{fora_sla_q} atrasados / críticos")
            with cards_gestao[1]:
                renderizar_indicador("Azix / MKTP · Pendentes", pend_a, f"{fora_sla_a} atrasados / críticos")
            with cards_gestao[2]:
                renderizar_indicador("Ativas · Pendentes", pend_ativas, f"{prio1_ativas} com prioridade 1 · {ativas_concluidos_periodo} concluídos no período")
            with cards_gestao[3]:
                renderizar_indicador("Concluídos no período", total_feitos_periodo, f"{data_inicio:%d/%m} a {data_fim:%d/%m}")

            c_graf1, c_graf2 = st.columns(2)
            with c_graf1:
                st.markdown("**Status Atual da Operação (Qualitor + Azix + Ativas)**")
                if not df_qualitor.empty and not df_azix_data.empty:
                    status_comb = pd.concat([df_qualitor['Status'], df_azix_data['Status']])
                    if not df_ativas_data.empty:
                        status_comb = pd.concat([status_comb, df_ativas_data['Status']])
                    st.bar_chart(status_comb.value_counts(), width="stretch")
                else: st.info("Sem dados")
            with c_graf2:
                st.markdown(f"**Top Produtividade ({data_inicio.strftime('%d/%m')} a {data_fim.strftime('%d/%m')})**")
                if not ranking_periodo.empty: st.bar_chart(ranking_periodo.set_index('Nome'), width="stretch")
                else: st.info("Sem dados para o período selecionado.")

            # --- NOVA VISUALIZAÇÃO: VOLUME POR ETAPA (QUALITOR) ---
            st.write("---")
            st.markdown("### 📊 Detalhamento de Etapas (Fila Qualitor)")
            if not df_qualitor.empty and 'Etapa' in df_qualitor.columns:
                df_pendentes_qualitor = df_qualitor[df_qualitor['Status'] == 'Pendente']
                if not df_pendentes_qualitor.empty:
                    contagem_etapas = df_pendentes_qualitor['Etapa'].value_counts().reset_index()
                    contagem_etapas.columns = ['Etapa (Assunto)', 'Quantidade na Fila']
                
                    c_etapa1, c_etapa2 = st.columns([2, 1])
                    with c_etapa1:
                        st.bar_chart(df_pendentes_qualitor['Etapa'].value_counts(), width="stretch")
                    with c_etapa2:
                        st.dataframe(contagem_etapas, hide_index=True, width="stretch")
                else:
                    st.success("Não há chamados pendentes na fila do Qualitor no momento.")
            else:
                st.info("A base Qualitor está vazia ou sem a coluna 'Etapa'.")

            # --- HEADCOUNT E DIMENSIONAMENTO (WFM SEPARADO E REFINADO) ---
            st.write("---")
            st.subheader("👥 Força de Trabalho e Dimensionamento (WFM)")
        
            hoje_str = data_hoje()
            logs_do_dia = df_logs[df_logs['DataHora'].astype(str).str.contains(hoje_str)] if not df_logs.empty else pd.DataFrame()
        
            qtd_azix_hoje = 0
            qtd_qualitor_hoje = 0
            qtd_ativas_hoje = 0
            nomes_azix = ""
            nomes_qualitor = ""
            nomes_ativas = ""
        
            if not logs_do_dia.empty:
                usuarios_logados = logs_do_dia['Usuario'].unique()
                ops_reais = [u for u in usuarios_logados if u not in ADMINS and u != "TV"]
            
                # SEPARAÇÃO RÍGIDA DOS SQUADS
                ops_azix = [u for u in ops_reais if u in SQUAD_AZIX or u in SQUAD_MKTP]
                ops_ativas = [u for u in ops_reais if u in SQUAD_ATIVAS]
                ops_qualitor = [u for u in ops_reais if u not in SQUAD_AZIX and u not in SQUAD_MKTP and u not in SQUAD_ATIVAS]
            
                qtd_azix_hoje = len(ops_azix)
                qtd_qualitor_hoje = len(ops_qualitor)
                qtd_ativas_hoje = len(ops_ativas)
            
                nomes_azix = ", ".join(ops_azix) if qtd_azix_hoje > 0 else "Nenhum"
                nomes_qualitor = ", ".join(ops_qualitor) if qtd_qualitor_hoje > 0 else "Nenhum"
                nomes_ativas = ", ".join(ops_ativas) if qtd_ativas_hoje > 0 else "Nenhum"

            c_wfm1, c_wfm2, c_wfm3 = st.columns(3)
        
            with c_wfm1:
                st.markdown(f"""
                <div style='background-color: #f0f9ff; padding: 15px; border-radius: 8px; border-left: 5px solid #0284c7; height: 100%;'>
                    <p style='margin:0; color: #0369a1; font-size: 0.9em;'>Operadores Qualitor (Hoje):</p>
                    <h2 style='margin:0; color: #0f172a;'>👨‍💻 {qtd_qualitor_hoje}</h2>
                    <p style='margin-top:10px; font-size: 0.75em; color: #475569;'><b>Nomes:</b> {nomes_qualitor}</p>
                </div>
                """, unsafe_allow_html=True)
        
            with c_wfm2:
                st.markdown(f"""
                <div style='background-color: #fff7ed; padding: 15px; border-radius: 8px; border-left: 5px solid #d97706; height: 100%;'>
                    <p style='margin:0; color: #b45309; font-size: 0.9em;'>Operadores Azix/Mktp (Hoje):</p>
                    <h2 style='margin:0; color: #0f172a;'>👨‍💻 {qtd_azix_hoje}</h2>
                    <p style='margin-top:10px; font-size: 0.75em; color: #475569;'><b>Nomes:</b> {nomes_azix}</p>
                </div>
                """, unsafe_allow_html=True)

            with c_wfm3:
                st.markdown(f"""
                <div style='background-color: #faf5ff; padding: 15px; border-radius: 8px; border-left: 5px solid #8b5cf6; height: 100%;'>
                    <p style='margin:0; color: #6b21a8; font-size: 0.9em;'>Operadores Ativas Mktp (Hoje):</p>
                    <h2 style='margin:0; color: #0f172a;'>👨‍💻 {qtd_ativas_hoje}</h2>
                    <p style='margin-top:10px; font-size: 0.75em; color: #475569;'><b>Nomes:</b> {nomes_ativas}</p>
                </div>
                """, unsafe_allow_html=True)

            st.write("")
            st.markdown("### ⏱️ Calculadoras de Dimensionamento (WFM)")
            col_calc1, col_calc2 = st.columns(2)

        

            # 1. CALCULADORA QUALITOR
            with col_calc1:
                st.markdown("<div style='background-color: #f8fafc; padding:15px; border-radius:8px; border:1px solid #e2e8f0;'>", unsafe_allow_html=True)
                st.markdown("<p style='margin:0; color: #0284c7; font-weight: bold;'>🔷 Calculadora Qualitor (SAC)</p>", unsafe_allow_html=True)
                col_tq, col_hq = st.columns(2)
                tma_q = col_tq.number_input("TMA Qualitor (min):", min_value=1, value=8, key="tma_q")
                hora_fim_q = col_hq.time_input("Fim Turno Qualitor:", value=datetime.strptime("18:00", "%H:%M").time(), key="hf_q")
            
                agora = hora_brasil()
                fim_q_dt = agora.replace(hour=hora_fim_q.hour, minute=hora_fim_q.minute, second=0, microsecond=0)
                minutos_q = int((fim_q_dt - agora).total_seconds() / 60)
            
                if minutos_q > 0 and pend_q > 0:
                    p_necessarias_q = np.ceil((pend_q * tma_q) / minutos_q)
                    st.info(f"**Ideal:** {int(p_necessarias_q)} operadores p/ zerar {pend_q} chamados.")
                    if qtd_qualitor_hoje < p_necessarias_q:
                        st.error(f"⚠️ Faltam {int(p_necessarias_q - qtd_qualitor_hoje)} operadores no Qualitor!")
                    else:
                        st.success("✅ Equipe Qualitor dimensionada corretamente!")
                elif pend_q == 0: st.success("🎉 Fila Qualitor zerada!")
                else: st.error("⏰ Expediente encerrado.")
                st.markdown("</div>", unsafe_allow_html=True)

            # 2. CALCULADORA ATIVAS MKTP
            with col_calc2:
                st.markdown("<div style='background-color: #f8fafc; padding:15px; border-radius:8px; border:1px solid #e2e8f0;'>", unsafe_allow_html=True)
                st.markdown("<p style='margin:0; color: #8b5cf6; font-weight: bold;'>🎯 Calculadora Ativas Mktp</p>", unsafe_allow_html=True)
                col_ta, col_ha = st.columns(2)
                tma_a = col_ta.number_input("TMA Ativas (min):", min_value=1, value=5, key="tma_a")
                hora_fim_a = col_ha.time_input("Fim Turno Ativas:", value=datetime.strptime("18:00", "%H:%M").time(), key="hf_a")
            
                fim_a_dt = agora.replace(hour=hora_fim_a.hour, minute=hora_fim_a.minute, second=0, microsecond=0)
                minutos_a = int((fim_a_dt - agora).total_seconds() / 60)
            
                if minutos_a > 0 and pend_ativas > 0:
                    p_necessarias_a = np.ceil((pend_ativas * tma_a) / minutos_a)
                    st.info(f"**Ideal:** {int(p_necessarias_a)} operadores p/ zerar {pend_ativas} pedidos.")
                    if qtd_ativas_hoje < p_necessarias_a:
                        st.error(f"⚠️ Faltam {int(p_necessarias_a - qtd_ativas_hoje)} operadores nas Ativas!")
                    else:
                        st.success("✅ Equipe Ativas dimensionada corretamente!")
                elif pend_ativas == 0: st.success("🎉 Fila Ativas zerada!")
                else: st.error("⏰ Expediente encerrado.")
                st.markdown("</div>", unsafe_allow_html=True)

           # ====================================================================
            # ⏱️ NOVO MÓDULO: MOTOR DE TMA (ON-DEMAND / A PEDIDO)
            # ====================================================================
            st.write("---")
            with st.expander("⏱️ Desempenho de Tempo (TMA / TMT) - Clique para Abrir", expanded=False):
                st.info("💡 Para proteger a memória do servidor, o TMA não é calculado automaticamente. Selecione a data na Máquina do Tempo lá em cima e clique no botão abaixo.")
            
                if st.button("🚀 Calcular TMA do Período", type="primary"):
                    if not df_logs_periodo.empty:
                        with st.spinner("Analisando logs e calculando tempos médios..."):
                            try:
                                df_tma = df_logs_periodo.copy()
                            
                                # 1. O Robô caça os números dos chamados dentro dos textos dos logs
                                df_tma['ID_Chamado'] = df_tma['Acao'].astype(str).str.extract(r'(\d+)')
                            
                                # 2. O Robô separa o que é clique de Início e o que é clique de Fim
                                def classificar_acao(texto):
                                    texto = str(texto).lower()
                                    if 'pegou' in texto or 'busca ativa' in texto: return 'Inicio'
                                    if any(x in texto for x in ['finalizou', 'concluiu', 'encerrada']): return 'Fim'
                                    return 'Outro'
                            
                                df_tma['Tipo_Acao'] = df_tma['Acao'].apply(classificar_acao)
                            
                                # 3. Filtra apenas as linhas úteis
                                df_calc = df_tma[(df_tma['ID_Chamado'].notna()) & (df_tma['Tipo_Acao'].isin(['Inicio', 'Fim']))].copy()
                            
                                if not df_calc.empty:
                                    df_calc['DataHora_DT'] = pd.to_datetime(df_calc['DataHora'], format="%d/%m/%Y %H:%M:%S", errors='coerce')
                                
                                    # 4. Junta o Início e o Fim na mesma linha para calcular a diferença
                                    tma_pivot = df_calc.pivot_table(index=['Usuario', 'ID_Chamado'], columns='Tipo_Acao', values='DataHora_DT', aggfunc='first').reset_index()
                                
                                    if 'Inicio' in tma_pivot.columns and 'Fim' in tma_pivot.columns:
                                        # A MATEMÁTICA DO TEMPO!
                                        tma_pivot['Duracao_Minutos'] = (tma_pivot['Fim'] - tma_pivot['Inicio']).dt.total_seconds() / 60.0
                                        tma_pivot = tma_pivot[tma_pivot['Duracao_Minutos'] > 0] # Remove erros e cliques duplicados rápidos
                                    
                                        if not tma_pivot.empty:
                                            # Define de qual squad a pessoa é
                                            def definir_squad(user):
                                                if user in SQUAD_AZIX or user in SQUAD_MKTP: return "🔶 Azix/Mktp"
                                                if user in SQUAD_ATIVAS: return "🎯 Ativas Mktp"
                                                return "🔷 Qualitor"
                                        
                                            tma_pivot['Equipe'] = tma_pivot['Usuario'].apply(definir_squad)
                                        
                                            # Agrupamentos Matemáticos
                                            tma_geral = tma_pivot['Duracao_Minutos'].mean()
                                            tma_por_squad = tma_pivot.groupby('Equipe')['Duracao_Minutos'].mean().reset_index()
                                            tma_por_user = tma_pivot.groupby(['Equipe', 'Usuario'])['Duracao_Minutos'].agg(['mean', 'count']).reset_index()
                                            tma_por_user.columns = ['Equipe', 'Operador', 'TMA_Minutos', 'Chamados_Medidos']
                                            tma_por_user = tma_por_user.sort_values(by=['Equipe', 'TMA_Minutos'])
                                        
                                            # Formatador bonito de tempo
                                            def formatar_tma(minutos):
                                                if pd.isna(minutos): return "-"
                                                m = int(minutos)
                                                s = int((minutos - m) * 60)
                                                return f"{m}m {s}s"
                                        
                                            # --- DESENHA NA TELA ---
                                            col_t1, col_t2 = st.columns([1, 2])
                                        
                                            with col_t1:
                                                st.markdown(f"<h3 style='color: #475569;'>TMA Global: <span style='color: #0f172a;'>{formatar_tma(tma_geral)}</span></h3>", unsafe_allow_html=True)
                                                st.write("**TMA Médio por Equipe:**")
                                                tma_por_squad['TMA Visual'] = tma_por_squad['Duracao_Minutos'].apply(formatar_tma)
                                                st.dataframe(tma_por_squad[['Equipe', 'TMA Visual']], hide_index=True, width="stretch")
                                            
                                            with col_t2:
                                                st.write("**👨‍💻 TMA Individual dos Operadores:**")
                                                tma_por_user['TMA Visual'] = tma_por_user['TMA_Minutos'].apply(formatar_tma)
                                                st.dataframe(tma_por_user[['Equipe', 'Operador', 'TMA Visual', 'Chamados_Medidos']], hide_index=True, width="stretch")
                                        else:
                                            st.info("⏳ Aguardando os operadores finalizarem os primeiros chamados para gerar as médias.")
                                    else:
                                        st.info("⏳ Recolhendo dados de Início e Fim das tratativas atuais...")
                            except Exception as e:
                                st.error(f"Erro ao calcular TMA: {e}")
                    else:
                        st.warning("Sem logs no período selecionado para calcular o TMA.")

            # --- NOVA VISUALIZAÇÃO: MÉTRICAS DE VALIDAÇÃO DA RECEITA (AZIX) ---
            st.write("---")
            st.markdown("### 🏛️ Validação de Endereço na Receita (Azix)")
        
            if not df_azix_data.empty and 'Validacao_Receita' in df_azix_data.columns:
                # Puxamos os dados da coluna e limpamos espaços vazios
                df_val = df_azix_data.copy()
                df_val['Validacao_Receita'] = df_val['Validacao_Receita'].astype(str).str.strip()
            
                # 1. Quantos entram (Total na base do Azix)
                total_entraram = len(df_val)
            
                # Matemática dos Status
                aceitaram = len(df_val[df_val['Validacao_Receita'] == 'Cliente Aceitou'])
                reprovaram = len(df_val[df_val['Validacao_Receita'] == 'Cliente Negou'])
                aguardando = len(df_val[df_val['Validacao_Receita'] == 'Aguardando Cliente'])
                sla_esgotado = len(df_val[df_val['Validacao_Receita'] == 'SLA Esgotado'])
            
                # 2. Quantos trataram (Soma de todos que já ganharam um status diferente de vazio/Não Tratado)
                trataram_total = aceitaram + reprovaram + aguardando + sla_esgotado
            
                # Cards na tela
                linha_1 = st.columns(3)
                linha_1[0].metric("📥 Base Total (Entraram)", total_entraram)
                linha_1[1].metric("⚙️ Já Tratados", trataram_total)
                linha_1[2].metric("✅ Aceitaram", aceitaram)

                # Segunda linha de indicadores
                linha_2 = st.columns(3)

                linha_2[0].metric("❌ Reprovaram", reprovaram)
                linha_2[1].metric("⏳ Aguardando", aguardando)
                linha_2[2].metric("⏰ SLA Esgotado", sla_esgotado)
            
                # Gráfico de Validação
                if trataram_total > 0:
                    dados_grafico_val = pd.DataFrame({
                        'Status': ['Aceitou', 'Negou', 'Aguardando', 'SLA Esgotado'],
                        'Quantidade': [aceitaram, reprovaram, aguardando, sla_esgotado]
                    }).set_index('Status')
                    st.bar_chart(dados_grafico_val, width="stretch", color="#d97706")
            else:
                st.info("Aguardando as primeiras validações da equipa ou a coluna 'Validacao_Receita' ainda não foi lida na base.")

            with st.expander("Informações do servidor"):
                st.caption("Monitor local desativado. Acompanhe a saúde pelos logs do Streamlit Cloud.")

        elif secao_gestao == "Importar bases":
            # --- ROBÔ IMPORTADOR ---
            st.write("---")
            st.subheader("Importar bases")
            tipo_importacao = st.radio("Escolha a Base:", ["1. Qualitor (Substituição)", "2. Azix (Injeção Inteligente)"])
        
            with st.container(border=True, key="frg-import"):
                arquivo_excel = st.file_uploader("Arraste o arquivo aqui (.xlsx ou .csv)", type=["xlsx", "xls", "csv"])
                if arquivo_excel is not None:
                    try:
                        with st.spinner("Lendo..."):
                            if arquivo_excel.name.endswith('.csv'): df_bruto = pd.read_csv(arquivo_excel, sep=None, engine='python', encoding='latin-1')
                            else: df_bruto = pd.read_excel(arquivo_excel)
                    
                        if tipo_importacao == "1. Qualitor (Substituição)":
                            if 'PROCESSO' in df_bruto.columns and 'Chamado' in df_bruto.columns:
                                df_pronto, resumo_qualitor = preparar_importacao_qualitor(df_bruto)
                            
                                qtd_novos = len(df_pronto)
                                qtd_prioridade = len(df_pronto[df_pronto['SLA'].str.contains("Prioridade")])
                            
                                st.success("✅ Análise Qualitor concluída!")
                                st.info(
                                    f"Lidos: {resumo_qualitor['lidos']} | "
                                    f"Ignorados (Solicitante): {resumo_qualitor['ignorados_solicitante']} | "
                                    f"Duplicados: {resumo_qualitor['duplicados']} | "
                                    f"Prontos para importar: {resumo_qualitor['importados']}"
                                )
                                st.metric(ETAPA_AGING_QUALITOR, resumo_qualitor['aging_maior_20'])
                                st.caption(
                                    f"Destes, {resumo_qualitor['aging_prioritarios']} mantêm prioridade no SLA. "
                                    f"Etapas preservadas: {resumo_qualitor['estornados']} Estornados e "
                                    f"{resumo_qualitor['reembolso_mktp']} Reembolso MKTP. "
                                    "O aging corresponde ao valor salvo na planilha enviada."
                                )
                                cc1, cc2 = st.columns(2)
                                cc1.markdown(f"<div style='background-color: #f8fafc; padding: 15px; border-radius: 8px; border: 1px solid #e2e8f0;'><h3 style='margin:0; color:#0f172a;'>📦 {qtd_novos}</h3><p style='margin:0; color:#64748b;'>Chamados Identificados</p></div>", unsafe_allow_html=True)
                            
                                if 'Etapa' in df_pronto.columns:
                                    top_etapas = df_pronto['Etapa'].value_counts().head(3)
                                    txt_etapas = "<br>".join([f"<b>{k}:</b> {v}" for k, v in top_etapas.items()])
                                    cc2.markdown(f"<div style='background-color: #f8fafc; padding: 15px; border-radius: 8px; border: 1px solid #e2e8f0;'><p style='margin:0; color:#64748b; font-size:0.9em;'>Top Assuntos:</p><div style='color:#0f172a;'>{txt_etapas}</div></div>", unsafe_allow_html=True)
                            
                                st.write("")
                                if qtd_prioridade > 0:
                                    st.error(f"🔥 ALERTA: Temos **{qtd_prioridade}** chamados com PRIORIDADE MÁXIMA (Vence Hoje) neste lote. Foco total!")

                                with st.expander("Conferir classificação antes de substituir"):
                                    st.dataframe(
                                        df_pronto[['Dados', 'Etapa', 'Motivo_Original', 'Aging_Abertura', 'SLA']].head(50),
                                        hide_index=True,
                                        width="stretch",
                                    )
                                    st.caption("Prévia dos primeiros 50 chamados. A importação inclui todos os registros válidos.")
                                if df_pronto.empty:
                                    st.warning("Nenhum chamado válido para importar. A base atual será mantida.")
                                st.caption("Substitua somente após a equipe encerrar os atendimentos da base atual.")
                                if st.button("🚀 SUBSTITUIR BASE QUALITOR", type="primary", disabled=df_pronto.empty):
                                    df_limpo = df_pronto.fillna("").replace(['nan', 'NaN', 'NaT', 'None'], "")
                                    limpar_reservas_aba(aba_chamados)
                                    aba_chamados.clear(); aba_chamados.append_rows([df_limpo.columns.tolist()] + df_limpo.values.tolist())
                                    registrar_log(usuario, "Importou Base Qualitor")
                                    st.success("Atualizado!"); invalidar_cache_base("qualitor"); st.rerun()
                            else: st.error("Erro: Colunas 'Chamado' ou 'PROCESSO' ausentes na planilha.")
                    
                        else:
                            if aba_azix is None: st.error("Aba 'Azix' não existe!"); st.stop()
                            df_bruto = df_bruto.fillna("").astype(str).replace(['nan', 'NaN', 'NaT', 'None'], "")
                        
                            if not df_azix_data.empty and 'Nº Pedido venda' in df_azix_data.columns:
                                existentes = df_azix_data['Nº Pedido venda'].astype(str).tolist()
                                df_novos = df_bruto[~df_bruto['Nº Pedido venda'].astype(str).isin(existentes)].copy()
                                is_merge = True
                            else:
                                df_novos = df_bruto.copy()
                                is_merge = False
                            
                            qtd_total_lidos = len(df_bruto)
                            qtd_novos = len(df_novos)
                            qtd_existentes = qtd_total_lidos - qtd_novos
                        
                            st.success("✅ Análise Azix concluída!")
                            st.info(f"📊 **Resumo da Importação:** Lidos: {qtd_total_lidos} | Já existiam na base (Ignorados): {qtd_existentes} | **NOVOS para Injetar: {qtd_novos}**")
                        
                            st.write("")
                            if qtd_novos > 0 and st.button("🚀 ADICIONAR NOVOS PEDIDOS (AZIX)", type="primary"):
                                df_novos['Status'] = "Pendente"
                                df_novos['Responsavel'] = ""
                                df_novos['Inicio'] = ""
                                df_novos['Data_Conclusao'] = ""
                                df_novos['Assentamentos'] = ""
                                df_novos['Data_Entrada'] = data_hoje() 
                            
                                if is_merge:
                                    cols_exist = df_azix_data.columns.tolist()
                                    for c in cols_exist:
                                        if c not in df_novos.columns: df_novos[c] = ""
                                    df_novos = df_novos[cols_exist]
                                    aba_azix.append_rows(df_novos.values.tolist())
                                else:
                                    aba_azix.clear(); aba_azix.append_rows([df_novos.columns.tolist()] + df_novos.values.tolist())
                            
                                registrar_log(usuario, f"Adicionou {len(df_novos)} Azix")
                                st.success("Fila Azix atualizada!"); invalidar_cache_base("azix"); st.rerun()
                    except Exception as e: st.error(f"Erro no robô: {e}")


        elif secao_gestao == "Exportações":
            # --- EXPORTAÇÃO COMPLETA COM FILTRO ---
            st.write("---")
            st.subheader("💾 Exportação de Bases e Auditoria")
        
            # Função para converter DataFrame em Excel em memória
            import io
            def df_para_excel(df_export):
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer:
                    df_export.to_excel(writer, index=False, sheet_name='Sheet1')
                return output.getvalue()

            cexp1, cexp2, cexp3 = st.columns(3)
        
            with cexp1:
                if not df_qualitor.empty:
                    if st.button("🛠️ Preparar Qualitor", key="preparar_excel_q", width="stretch"):
                        st.session_state["excel_q"] = df_para_excel(df_qualitor)
                    if "excel_q" in st.session_state:
                        st.download_button("📥 BAIXAR QUALITOR", data=st.session_state["excel_q"], file_name=f"Qualitor_{data_hoje().replace('/','-')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")

            with cexp2:
                if not df_azix_data.empty:
                    if st.button("🛠️ Preparar Azix/MKTP", key="preparar_excel_a", width="stretch"):
                        st.session_state["excel_a"] = df_para_excel(df_azix_data)
                    if "excel_a" in st.session_state:
                        st.download_button("📥 BAIXAR AZIX/MKTP", data=st.session_state["excel_a"], file_name=f"Azix_{data_hoje().replace('/','-')}.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")

            with cexp3:
                if not df_logs_periodo.empty:
                    if st.button("🛠️ Preparar Logs", key="preparar_excel_logs", width="stretch"):
                        df_logs_export = df_logs_periodo.drop(columns=['DataReal'])
                        st.session_state["excel_logs"] = df_para_excel(df_logs_export)
                    if "excel_logs" in st.session_state:
                        nome_arquivo_logs = f"Logs_{data_inicio.strftime('%d%m')}_a_{data_fim.strftime('%d%m')}.xlsx"
                        st.download_button("📥 BAIXAR LOGS", data=st.session_state["excel_logs"], file_name=nome_arquivo_logs, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
                else:
                    st.info("Sem logs para este período.")

            # --- GERADOR DE RELATÓRIO EXECUTIVO (PDF) ---
            st.write("---")
            with st.expander("📄 Gerar Relatório Executivo Oficial (PDF)"):
                aviso_pdf = st.text_input("Observação (Opcional):", placeholder="Ex: Operação Azix com volume alto...")
                if st.button("🖨️ Mapear Dados e Criar PDF", width="stretch"):
                    with st.spinner("Compilando dados em tempo real..."):
                        def formatar_texto(texto): return unicodedata.normalize('NFKD', str(texto)).encode('ASCII', 'ignore').decode('ASCII')
                    
                        pdf = FPDF()
                        pdf.add_page()
                        pdf.set_font('Arial', 'B', 16); pdf.cell(0, 10, formatar_texto('Relatorio Executivo - Operacao SAC & Azix'), 0, 1, 'C')
                        pdf.set_font('Arial', 'I', 10); pdf.cell(0, 10, formatar_texto(f'Gerado em: {hora_texto()}'), 0, 1, 'C'); pdf.ln(10)
                    
                        pdf.set_font('Arial', 'B', 12); pdf.cell(0, 10, formatar_texto('1. OPERACAO QUALITOR (TEMPO REAL)'), 0, 1)
                        pdf.set_font('Arial', '', 12)
                        pdf.cell(0, 10, formatar_texto(f'> Fila Atual: {pend_q} pendentes. (Criticos/Atrasados: {fora_sla_q})'), 0, 1)
                        pdf.ln(5)
                    
                        pdf.set_font('Arial', 'B', 12); pdf.cell(0, 10, formatar_texto('2. OPERACAO AZIX / MARKETPLACE (TEMPO REAL)'), 0, 1)
                        pdf.set_font('Arial', '', 12)
                        pdf.cell(0, 10, formatar_texto(f'> Fila Atual: {pend_a} tratativas pendentes. (Criticas/Atrasadas: {fora_sla_a})'), 0, 1)
                        pdf.ln(5)
                    
                        pdf.set_font('Arial', 'B', 12); pdf.cell(0, 10, formatar_texto(f'3. DESTAQUES DA EQUIPE ({data_inicio.strftime("%d/%m")} a {data_fim.strftime("%d/%m")})'), 0, 1)
                        pdf.set_font('Arial', '', 12)
                        if not ranking_periodo.empty:
                            for i, row in ranking_periodo.head(3).iterrows(): pdf.cell(0, 10, formatar_texto(f"{i+1} Lugar: {row['Nome']} - {row['Qtd']} concluídos"), 0, 1)
                        else: pdf.cell(0, 10, formatar_texto('Sem finalizacoes neste periodo.'), 0, 1)
                        pdf.ln(5)
                    
                        if aviso_pdf:
                            pdf.set_font('Arial', 'B', 12); pdf.cell(0, 10, formatar_texto('4. DIRETRIZ DA GESTAO'), 0, 1)
                            pdf.set_font('Arial', 'I', 11); pdf.multi_cell(0, 10, formatar_texto(aviso_pdf))
                        
                        nome_arquivo = f"Relatorio_Operacional_{datetime.now().strftime('%d%m%Y')}.pdf"
                        pdf.output(nome_arquivo, 'F')
                        with open(nome_arquivo, "rb") as f: bytes_pdf = f.read()
                        st.success("✅ PDF Gerado!")
                        st.download_button(label="📥 BAIXAR RELATÓRIO PDF", data=bytes_pdf, file_name=nome_arquivo, mime="application/pdf", type="primary")
            

        elif secao_gestao == "Manutenção":
            st.subheader("🧹 Limpeza e Ações de Emergência")
            if st.button("🧹 APAGAR TODOS OS CONCLUÍDOS (AZIX)"):
                if not df_azix_data.empty:
                    df_ativos = df_azix_data[df_azix_data['Status'] != 'Concluido'].copy()
                    limpar_reservas_aba(aba_azix)
                    aba_azix.clear()
                    aba_azix.append_rows([df_ativos.columns.tolist()] + df_ativos.values.tolist())
                    st.success("Faxina feita!"); invalidar_cache_base("azix"); st.rerun()
            if st.button(
                "🔓 LIMPAR RESERVAS ANTIGAS DO AZIX",
                key="limpar_reservas_antigas_azix"):
                limpar_reservas_aba(aba_azix)
                invalidar_cache_base("azix")
                st.success("✅ Reservas antigas do Azix removidas!")
                st.rerun()
                
            em_andamento = df_qualitor[df_qualitor['Status'] == 'Em Andamento'].copy() if not df_qualitor.empty else pd.DataFrame()
            if not em_andamento.empty:
                opcoes = em_andamento.apply(lambda x: f"L{x.name + 2} - ID {x['ID']} - {x['Dados']} ({x['Responsavel']})", axis=1).tolist()
                selecionado = st.selectbox("Selecione um chamado travado (Qualitor):", [""] + opcoes)
                if selecionado:
                    linha_trava = int(selecionado.split(" - ")[0].replace("L", ""))
                    col_dev, col_forcar = st.columns(2)
                    if col_dev.button("↩️ Devolver à Fila"):
                        aba_chamados.batch_update([
                            {"range": rowcol_to_a1(linha_trava, COL_STATUS), "values": [["Pendente"]]},
                            {"range": rowcol_to_a1(linha_trava, COL_RESP), "values": [[""]]},
                            {"range": rowcol_to_a1(linha_trava, COL_INICIO), "values": [[""]]},
                        ])
                        liberar_reserva(aba_chamados, linha_trava)
                        registrar_log(usuario, f"ADMIN: Devolveu linha {linha_trava}"); st.success("Devolvido!"); invalidar_cache_base("qualitor"); st.rerun()
                    if col_forcar.button("🏁 Forçar Conclusão"):
                        aba_chamados.batch_update([
                            {"range": rowcol_to_a1(linha_trava, COL_STATUS), "values": [["Concluido"]]},
                            {"range": rowcol_to_a1(linha_trava, COL_FIM), "values": [[hora_texto()]]},
                        ])
                        liberar_reserva(aba_chamados, linha_trava)
                        registrar_log(usuario, f"ADMIN: Forçou conclusão linha {linha_trava}"); st.success("Encerrado!"); invalidar_cache_base("qualitor"); st.rerun()


    # =========================================================================
    # 🧠 SQUAD 1: VISÃO DA CHARLENE (TRATATIVAS AZIX COM SLA E BUSCA ATIVA)
    # =========================================================================
    elif usuario in SQUAD_AZIX:
        if status_real != "Disponivel": st.warning(f"⚠️ **VOCÊ ESTÁ EM PAUSA ({status_real})**")
        else:
            if df.empty or 'Status' not in df.columns:
                st.info("📭 A base de dados Azix está vazia. O Gestor precisa importar os dados no Painel de Gestão.")
                if st.button("🔄 Recarregar Fila"): invalidar_cache_base("azix"); st.rerun()
            else:
                meu_chamado = df[(df['Status'] == 'Em Andamento') & (df['Responsavel'] == usuario)]
                if len(meu_chamado) > 0:
                    dados = meu_chamado.iloc[0]
                    idx_linha = int(meu_chamado.index[0]) + 2 
                    
                    sla_badge = dados.get('Status_SLA', 'SLA não calculado')
                    if 'Atrasado' in sla_badge or 'Vence Hoje' in sla_badge: st.error(f"SLA: {sla_badge}")
                    else: st.info(f"SLA: {sla_badge}")
                    
                    st.markdown("### 📋 Ficha Detalhada do Pedido")
                    with st.container(border=True, key="frg-details-azix"):
                        for col in df.columns:
                            if col not in ['ID', 'Status', 'Responsavel', 'Inicio', 'Data_Conclusao', 'SLA', 'Peso_SLA', 'Assentamentos', 'Data_Entrada', 'Status_SLA', 'Validacao_Receita']:
                                val = dados.get(col, '')
                                if pd.notna(val) and str(val).strip() != '': st.markdown(f"**{col}:** {val}")

                    st.markdown("### 📝 Histórico (CRM)")
                    historico_atual = str(dados.get('Assentamentos', ''))
                    if historico_atual.strip() != '' and historico_atual != 'nan':
                        historico_formatado = re.sub(r'(\[\d{2}/\d{2}/\d{4})', r'\n\n\1', historico_atual).strip()
                        st.info(historico_formatado)
                    
                    # --- NOVA VALIDAÇÃO DA RECEITA ---
                    st.markdown("#### 🏛️ Validação Endereço Receita")
                    st.caption("Preencha para gerar as métricas de divergência de endereço.")
                    val_atual = str(dados.get('Validacao_Receita', 'Não Tratado'))
                    opcoes_val = ["Não Tratado", "Cliente Aceitou", "Cliente Negou", "Aguardando Cliente", "SLA Esgotado"]
                    idx_val = opcoes_val.index(val_atual) if val_atual in opcoes_val else 0
                    escolha_validacao = st.radio("Selecione o status desta tratativa:", opcoes_val, index=idx_val, horizontal=True)

                    novo_assentamento = st.text_area("Nova observação:", placeholder="Ex: Cliente contactado...")

                    st.write("---")
                    if 'confirmar_azix' not in st.session_state: st.session_state['confirmar_azix'] = False
                    
                    if not st.session_state['confirmar_azix']:
                        c_fim, c_pausa = st.columns(2)
                        if c_fim.button("✅ FINALIZAR TRATATIVA", type="primary", width="stretch"): st.session_state['confirmar_azix'] = True; st.rerun()
                        if c_pausa.button("⏳ DEVOLVER À FILA", width="stretch"):
                            atualizacoes = [
                                {"range": rowcol_to_a1(idx_linha, COL_STATUS), "values": [["Pendente - Retorno"]]},
                                {"range": rowcol_to_a1(idx_linha, COL_RESP), "values": [[""]]},
                                {"range": rowcol_to_a1(idx_linha, COL_INICIO), "values": [[""]]},
                            ]
                            if 'Validacao_Receita' in df.columns:
                                col_val_idx = df.columns.tolist().index('Validacao_Receita') + 1
                                atualizacoes.append({"range": rowcol_to_a1(idx_linha, col_val_idx), "values": [[escolha_validacao]]})
                            
                            if novo_assentamento:
                                col_ass = df.columns.tolist().index('Assentamentos') + 1
                                novo_historico = f"{historico_atual}\n[{hora_texto()}] {usuario}: {novo_assentamento}".strip()
                                atualizacoes.append({"range": rowcol_to_a1(idx_linha, col_ass), "values": [[novo_historico]]})

                            aba_atual.batch_update(atualizacoes)
                            liberar_reserva(aba_atual, idx_linha)
                            
                            num_pedido = str(dados.get('Nº Pedido venda', dados.get('Dados', '')))
                            if 'ignorados_azix' not in st.session_state: st.session_state['ignorados_azix'] = []
                            st.session_state['ignorados_azix'].append(num_pedido)
                            
                            registrar_log(usuario, f"Devolveu Azix à Fila ({num_pedido})"); invalidar_cache_base("azix"); st.rerun()
                    else:
                        st.warning("Confirma a conclusão?")
                        cy, cn = st.columns(2)
                        if cy.button("👍 SIM, FINALIZAR"):
                            atualizacoes = []
                            if 'Validacao_Receita' in df.columns:
                                col_val_idx = df.columns.tolist().index('Validacao_Receita') + 1
                                atualizacoes.append({"range": rowcol_to_a1(idx_linha, col_val_idx), "values": [[escolha_validacao]]})

                            if novo_assentamento:
                                col_ass = df.columns.tolist().index('Assentamentos') + 1
                                novo_historico = f"{historico_atual}\n[{hora_texto()}] {usuario}: {novo_assentamento}".strip()
                                atualizacoes.append({"range": rowcol_to_a1(idx_linha, col_ass), "values": [[novo_historico]]})
                            
                            num_pedido = dados.get('Nº Pedido venda', dados.get('Dados', ''))
                            if is_marketplace(num_pedido):
                                atualizacoes.extend([
                                    {"range": rowcol_to_a1(idx_linha, COL_STATUS), "values": [["Aguardando Reivindicação"]]},
                                    {"range": rowcol_to_a1(idx_linha, COL_RESP), "values": [[""]]},
                                ])
                                registrar_log(usuario, f"Azix para Mktp ({num_pedido})"); st.success("Encaminhado para Reivindicações!")
                            else:
                                atualizacoes.extend([
                                    {"range": rowcol_to_a1(idx_linha, COL_STATUS), "values": [["Concluido"]]},
                                    {"range": rowcol_to_a1(idx_linha, COL_FIM), "values": [[hora_texto()]]},
                                ])
                                registrar_log(usuario, f"Concluiu Azix ({num_pedido})")
                                verificar_meta_baloes(usuario, ranking_global, META_DIARIA)

                            aba_atual.batch_update(atualizacoes)
                            liberar_reserva(aba_atual, idx_linha)
                            st.session_state['confirmar_azix'] = False; invalidar_cache_base("azix"); st.rerun()
                        if cn.button("❌ NÃO"): st.session_state['confirmar_azix'] = False; st.rerun()

                else:
                    # --- FILAS SEPARADAS (NOVOS E RETORNOS) ---
                    fila_novos = df[(df['Status'] == 'Pendente') & (df['Responsavel'] == "")].copy()
                    fila_retornos = df[(df['Status'] == 'Pendente - Retorno') & (df['Responsavel'] == "")].copy()
                    
                    with st.form("frg-busca-azix", border=True):
                        st.markdown("**Buscar pedido específico**")
                        c_busca, c_btn = st.columns([4, 1.5], vertical_alignment="bottom")
                        pedido_busca = c_busca.text_input("Nº do Pedido:", placeholder="Ex: MAGALU_123")
                        if c_btn.form_submit_button("Buscar e puxar", width="stretch"):
                            if pedido_busca.strip():
                                if 'Nº Pedido venda' in df.columns:
                                    fila_geral = df[(df['Status'].isin(['Pendente', 'Pendente - Retorno'])) & (df['Responsavel'] == "")]
                                    alvo = fila_geral[fila_geral['Nº Pedido venda'].astype(str).str.contains(pedido_busca.strip(), case=False, na=False)]
                                    if not alvo.empty:
                                        item = alvo.iloc[0]; idx_linha = int(item.name) + 2
                                        ok, mensagem = reservar_atendimento(aba_atual, idx_linha, usuario, "Em Andamento", COL_STATUS, COL_RESP, COL_INICIO)
                                        if ok:
                                            registrar_log(usuario, f"Busca Ativa: Pegou {pedido_busca}")
                                            st.success("Encontrado! Puxando para sua tela..."); invalidar_cache_base("azix"); st.rerun()
                                        else:
                                            invalidar_cache_base("azix"); st.error(mensagem)
                                    else: st.error("Pedido não encontrado na fila livre, ou já está com outro operador.")
                                else: st.error("Coluna 'Nº Pedido venda' não encontrada na base.")
                            else: st.warning("Digite um número de pedido.")
                    
                    # --- TABS PARA AS FILAS ---
                    tab1, tab2 = st.columns(2, gap="medium", border=True)
                    
                    with tab1:
                        renderizar_indicador("Novos pedidos", len(fila_novos), compacto=True)
                        if len(fila_novos) > 0:
                            if st.button("📥 PUXAR NOVO PEDIDO", type="primary", width="stretch", key="btn_novo"):
                                item = fila_novos.iloc[0]
                                idx_linha = int(item.name) + 2 
                                num_pedido = str(item.get('Nº Pedido venda', ''))
                                ok, mensagem = reservar_atendimento(aba_atual, idx_linha, usuario, "Em Andamento", COL_STATUS, COL_RESP, COL_INICIO)
                                if ok:
                                    registrar_log(usuario, f"Pegou Pedido Azix ({num_pedido})")
                                    invalidar_cache_base("azix"); st.rerun()
                                else:
                                    invalidar_cache_base("azix"); st.error(mensagem)
                        else: st.info("Nenhum pedido novo. Clique em 'Atualizar Fila' para checar.")
                    
                    with tab2:
                        renderizar_indicador("Pendentes de retorno", len(fila_retornos), compacto=True)
                        if len(fila_retornos) > 0:
                            if st.button("📥 PUXAR RETORNO", type="primary", width="stretch", key="btn_retorno"):
                                if 'ignorados_azix' not in st.session_state: st.session_state['ignorados_azix'] = []
                                fila_limpa = fila_retornos[~fila_retornos['Nº Pedido venda'].astype(str).isin(st.session_state['ignorados_azix'])]
                                
                                if not fila_limpa.empty: item = fila_limpa.iloc[0]
                                else: st.session_state['ignorados_azix'] = []; item = fila_retornos.iloc[0]

                                idx_linha = int(item.name) + 2 
                                num_pedido = str(item.get('Nº Pedido venda', ''))
                                ok, mensagem = reservar_atendimento(aba_atual, idx_linha, usuario, "Em Andamento", COL_STATUS, COL_RESP, COL_INICIO)
                                if ok:
                                    registrar_log(usuario, f"Pegou Retorno Azix ({num_pedido})")
                                    invalidar_cache_base("azix"); st.rerun()
                                else:
                                    invalidar_cache_base("azix"); st.error(mensagem)
                        else: st.info("Nenhum retorno. Clique em 'Atualizar Fila' para checar.")

    # =========================================================================
    # 🛒 SQUAD 2: VISÃO DOS OPERADORES DE REIVINDICAÇÕES MARKETPLACE
    # =========================================================================
    elif usuario in SQUAD_MKTP:
        if status_real != "Disponivel": st.warning(f"⚠️ **VOCÊ ESTÁ EM PAUSA ({status_real})**")
        else:
            if df.empty or 'Status' not in df.columns:
                st.info("📭 A base de dados Azix está vazia. O Gestor precisa importar os dados no Painel de Gestão.")
                if st.button("🔄 Recarregar Fila"): invalidar_cache_base("azix"); st.rerun()
            else:
                meu_chamado = df[(df['Status'] == 'Em Tratativa Mktp') & (df['Responsavel'] == usuario)]
                if len(meu_chamado) > 0:
                    dados = meu_chamado.iloc[0]
                    idx_linha = int(meu_chamado.index[0]) + 2 
                    num_pedido = dados.get('Nº Pedido venda', 'N/A')
                    
                    st.success("🟢 TRATANDO REIVINDICAÇÃO...")
                    st.markdown(f"### 📦 Pedido: **{num_pedido}**")
                    
                    with st.container(border=True, key="frg-details-mktp"):
                        for col in df.columns:
                            if col not in ['ID', 'Status', 'Responsavel', 'Inicio', 'Data_Conclusao', 'SLA', 'Peso_SLA', 'Assentamentos', 'Data_Entrada', 'Status_SLA']:
                                val = dados.get(col, '')
                                if pd.notna(val) and str(val).strip() != '': st.markdown(f"**{col}:** {val}")

                    st.markdown("### 📝 Histórico (CRM)")
                    historico_atual = str(dados.get('Assentamentos', ''))
                    if historico_atual.strip() != '' and historico_atual != 'nan':
                        historico_formatado = re.sub(r'(\[\d{2}/\d{2}/\d{4})', r'\n\n\1', historico_atual).strip()
                        st.info(historico_formatado)
                    novo_assentamento = st.text_area("Observação final:")

                    st.write("---")
                    if 'confirmar_mktp' not in st.session_state: st.session_state['confirmar_mktp'] = False
                    
                    if not st.session_state['confirmar_mktp']:
                        if st.button("✅ ENCERRAR REIVINDICAÇÃO", type="primary", width="stretch"): st.session_state['confirmar_mktp'] = True; st.rerun()
                    else:
                        st.warning("Confirma o encerramento?")
                        cy, cn = st.columns(2)
                        if cy.button("👍 SIM"):
                            atualizacoes = [
                                {"range": rowcol_to_a1(idx_linha, COL_STATUS), "values": [["Concluido"]]},
                                {"range": rowcol_to_a1(idx_linha, COL_FIM), "values": [[hora_texto()]]},
                            ]
                            if novo_assentamento:
                                col_ass = df.columns.tolist().index('Assentamentos') + 1
                                novo_historico = f"{historico_atual}\n[{hora_texto()}] {usuario}: {novo_assentamento}".strip()
                                atualizacoes.append({"range": rowcol_to_a1(idx_linha, col_ass), "values": [[novo_historico]]})
                            aba_atual.batch_update(atualizacoes)
                            liberar_reserva(aba_atual, idx_linha)
                            registrar_log(usuario, f"Reivindicação Encerrada")
                            verificar_meta_baloes(usuario, ranking_global, META_DIARIA)
                            st.session_state['confirmar_mktp'] = False
                            invalidar_cache_base("azix"); st.rerun()
                        if cn.button("❌ NÃO"): st.session_state['confirmar_mktp'] = False; st.rerun()
                else:
                    fila = df[(df['Status'] == 'Aguardando Reivindicação')].copy()
                    if not fila.empty and "Todas" not in minhas_etapas and "todas" not in [e.lower() for e in minhas_etapas]:
                        col_busca = 'Nº Pedido venda' if 'Nº Pedido venda' in fila.columns else 'Dados'
                        filtro = pd.Series(False, index=fila.index)
                        for palavra in minhas_etapas:
                            if palavra.strip(): filtro = filtro | fila[col_busca].astype(str).str.contains(palavra.strip(), case=False, na=False)
                        fila = fila[filtro]
                    
                    with st.form("frg-busca-mktp", border=True):
                        st.markdown("**Buscar pedido específico**")
                        c_busca, c_btn = st.columns([4, 1.5], vertical_alignment="bottom")
                        pedido_busca = c_busca.text_input("Nº do Pedido:", placeholder="Ex: MAGALU_123")
                        if c_btn.form_submit_button("Buscar e puxar", width="stretch"):
                            if pedido_busca.strip():
                                if 'Nº Pedido venda' in fila.columns:
                                    alvo = fila[fila['Nº Pedido venda'].astype(str).str.contains(pedido_busca.strip(), case=False, na=False)]
                                    if not alvo.empty:
                                        item = alvo.iloc[0]; idx_linha = int(item.name) + 2
                                        ok, mensagem = reservar_atendimento(aba_atual, idx_linha, usuario, "Em Tratativa Mktp", COL_STATUS, COL_RESP, COL_INICIO)
                                        if ok:
                                            registrar_log(usuario, f"Busca Ativa Mktp: Pegou {pedido_busca}")
                                            st.success("Encontrado! Puxando para sua tela..."); invalidar_cache_base("azix"); st.rerun()
                                        else:
                                            invalidar_cache_base("azix"); st.error(mensagem)
                                    else: st.error("Pedido não encontrado na sua fila de reivindicações.")
                                else: st.error("Coluna 'Nº Pedido venda' não encontrada na base.")
                            else: st.warning("Digite um número de pedido.")

                    area_fila, area_meta = st.columns([1.35, 1], gap="medium")
                    with area_fila:
                        with st.container(border=True, key="frg-queue-mktp"):
                            renderizar_indicador("Sua fila", len(fila), "Pedidos disponíveis para atendimento", compacto=True)
                            if st.button("🔄 Atualizar Fila"): invalidar_cache_base("azix"); st.rerun()
                            if len(fila) > 0:
                                if st.button("📥 REIVINDICAR PRÓXIMO", type="primary", width="content"):
                                    item = fila.iloc[0]
                                    idx_linha = int(item.name) + 2 
                                    num_pedido = str(item.get('Nº Pedido venda', '')) # Puxa o número do pedido
                                    ok, mensagem = reservar_atendimento(aba_atual, idx_linha, usuario, "Em Tratativa Mktp", COL_STATUS, COL_RESP, COL_INICIO)
                                    if ok:
                                        registrar_log(usuario, f"Pegou Mktp ({num_pedido})")
                                        invalidar_cache_base("azix"); st.rerun()
                                    else:
                                        invalidar_cache_base("azix"); st.error(mensagem)
                            else:  st.info("Fila vazia. Clique em 'Atualizar Fila' para checar.")
                    with area_meta:
                        with st.container(border=True, key="frg-goal-mktp"):
                            renderizar_meta(qtd_minha, META_DIARIA)

    # =========================================================================
    # 🎯 SQUAD NOVO: ATIVAS E REIVINDICAÇÕES (MARKETPLACE)
    # =========================================================================
    elif usuario in SQUAD_ATIVAS:
        if status_real != "Disponivel": st.warning(f"⚠️ **VOCÊ ESTÁ EM PAUSA ({status_real})**")
        else:
            if df.empty or 'Status' not in df.columns:
                st.info("📭 A base de dados Ativas está vazia.")
                if st.button("🔄 Recarregar Fila"): invalidar_cache_base("ativas"); st.rerun()
            else:
                meu_chamado = df[(df['Status'] == 'Em Andamento') & (df['Responsavel'] == usuario)]
                
                # --- TELA DE ATENDIMENTO ---
                if len(meu_chamado) > 0:
                    dados = meu_chamado.iloc[0]
                    idx_linha = int(meu_chamado.index[0]) + 2 
                    num_pedido = dados.get('Pedido', 'N/A')
                    tipo_atual = dados.get('Tipo_Atividade', 'N/A')
                    prio_atual = dados.get('Prioridade', '')
                    
                    st.success(f"🟢 EM ATENDIMENTO ({tipo_atual}) - Prioridade {prio_atual}")
                    st.markdown(f"### 📦 Pedido / ID: **{num_pedido}**")
                    
                    with st.container(border=True, key="frg-details-ativas"):
                        for col in df.columns:
                            if col not in ['ID', 'Status', 'Responsavel', 'Inicio', 'Data_Conclusao', 'Assentamentos']:
                                val = dados.get(col, '')
                                if pd.notna(val) and str(val).strip() != '': st.markdown(f"**{col}:** {val}")

                    st.markdown("### 📝 Registrar Atendimento")
                    historico_atual = str(dados.get('Assentamentos', ''))
                    if historico_atual.strip() != '' and historico_atual != 'nan':
                        st.info(historico_atual)
                    
                    novo_assentamento = st.text_area("Descreva a ação realizada:")

                    st.write("---")
                    if 'confirmar_ativa' not in st.session_state: st.session_state['confirmar_ativa'] = False
                    
                    if not st.session_state['confirmar_ativa']:
                        if st.button("✅ CONCLUIR ATENDIMENTO", type="primary", width="stretch"): st.session_state['confirmar_ativa'] = True; st.rerun()
                    else:
                        st.warning("Confirma a conclusão?")
                        cy, cn = st.columns(2)
                        if cy.button("👍 SIM"):
                            atualizacoes = [
                                {"range": rowcol_to_a1(idx_linha, COL_STATUS), "values": [["Concluido"]]},
                                {"range": rowcol_to_a1(idx_linha, COL_FIM), "values": [[hora_texto()]]},
                            ]
                            if novo_assentamento:
                                col_ass = df.columns.tolist().index('Assentamentos') + 1
                                novo_historico = f"{historico_atual}\n[{hora_texto()}] {usuario}: {novo_assentamento}".strip()
                                atualizacoes.append({"range": rowcol_to_a1(idx_linha, col_ass), "values": [[novo_historico]]})
                            aba_atual.batch_update(atualizacoes)
                            liberar_reserva(aba_atual, idx_linha)
                            registrar_log(usuario, f"Concluiu Ativa ({num_pedido})")
                            verificar_meta_baloes(usuario, ranking_global, META_DIARIA)
                            st.session_state['confirmar_ativa'] = False
                            invalidar_cache_base("ativas"); st.rerun()
                        if cn.button("❌ NÃO"): st.session_state['confirmar_ativa'] = False; st.rerun()
                
                # --- TELA DE FILA LIVRE ---
                else:
                    fila = df[(df['Status'] == 'Pendente') & (df['Responsavel'] == "")].copy()
                    
                    # 1. Filtra pelo "Bolo" correto (Chat vs Reclamação) usando as Etapas_Permitidas
                    if "Todas" not in minhas_etapas and "todas" not in [e.lower() for e in minhas_etapas]:
                        if 'Tipo_Atividade' in fila.columns:
                            fila = fila[fila['Tipo_Atividade'].astype(str).isin(minhas_etapas)]
                    
                    # 2. A MÁGICA DA PRIORIDADE (Organiza de 1 para cima)
                    if 'Prioridade' in fila.columns:
                        fila['Prioridade_Num'] = pd.to_numeric(fila['Prioridade'], errors='coerce').fillna(999)
                        fila = fila.sort_values(by='Prioridade_Num', ascending=True)

                    area_fila, area_meta = st.columns([1.35, 1], gap="medium")
                    with area_fila:
                        with st.container(border=True, key="frg-queue-ativas"):
                            renderizar_indicador("Sua fila", len(fila), "Pedidos disponíveis para atendimento", compacto=True)
                            if st.button("🔄 Atualizar Fila"): invalidar_cache_base("ativas"); st.rerun()
                    
                            if len(fila) > 0:
                                if st.button("📥 PUXAR PRÓXIMO (PRIORIDADE)", type="primary", width="content"):
                                    item = fila.iloc[0] # Pega sempre o topo da lista (Prioridade 1)
                                    idx_linha = int(item.name) + 2 
                                    num_pedido = str(item.get('Pedido', ''))
                                    ok, mensagem = reservar_atendimento(aba_atual, idx_linha, usuario, "Em Andamento", COL_STATUS, COL_RESP, COL_INICIO)
                                    if ok:
                                        registrar_log(usuario, f"Pegou Ativa ({num_pedido})")
                                        invalidar_cache_base("ativas"); st.rerun()
                                    else:
                                        invalidar_cache_base("ativas"); st.error(mensagem)
                            else:
                                st.success("Fila zerada para as suas habilidades!")
                                st.info("Fila zerada. Clique em 'Atualizar Fila' para checar.")
                    with area_meta:
                        with st.container(border=True, key="frg-goal-ativas"):
                            renderizar_meta(qtd_minha, META_DIARIA)

    # =========================================================================
    # 👷 SQUAD 3: VISÃO PADRÃO (OPERADOR QUALITOR)
    # =========================================================================
    else:
        if status_real != "Disponivel": st.warning(f"⚠️ **VOCÊ ESTÁ EM PAUSA ({status_real})**")
        else:
            
            if df.empty or 'Status' not in df.columns:
                st.info("📭 A base de dados Qualitor está vazia. O Gestor precisa importar os dados.")
                if st.button("🔄 Recarregar Fila"): invalidar_cache_base("qualitor"); st.rerun()
            else:
                meu_chamado = df[(df['Status'] == 'Em Andamento') & (df['Responsavel'] == usuario)]
                
                if len(meu_chamado) > 0:
                    dados = meu_chamado.iloc[0]
                    num = dados.get('Dados', 'N/A') 
                    sla_atual = str(dados.get('SLA', 'Sem Info'))
                    
                    if 'fora' in sla_atual.lower() or 'prioridade' in sla_atual.lower(): st.error(f"🔥 ALERTA DE SLA: {sla_atual}")
                    else: st.info(f"✅ Status do SLA: {sla_atual}")

                    st.markdown(f"### 📞 Chamado: **{num}** | Etapa: **{dados.get('Etapa', 'N/A')}**")
                    # Campos opcionais: a base anterior continua funcionando até a nova importação.
                    motivo_original = str(dados.get('Motivo_Original', '')).strip()
                    aging_importado = pd.to_numeric(dados.get('Aging_Abertura', None), errors='coerce')
                    if motivo_original:
                        st.caption(f"Motivo original: {motivo_original}")
                    if pd.notna(aging_importado) and np.isfinite(aging_importado):
                        st.caption(f"Aging na importação: {aging_importado:g} dias úteis")
                    if str(num) != 'N/A': 
                        link_q = f"https://frigelar.qualitorsoftware.com/html/hd/hdchamado/cadastro_chamado.php?cdchamado={num}"
                        
                        st.markdown("<span style='font-size: 0.9em;'>Impossibilitado temporariamente via link direto</span>", unsafe_allow_html=True)
                        st.markdown("1️⃣ Clique no **ícone de copiar** no canto superior direito da caixa abaixo.<br>2️⃣ Pressione **Ctrl + T** (Nova Aba) e **Ctrl + V** (Colar).", unsafe_allow_html=True)
                        st.code(link_q, language="text")
                    
                    with st.expander("✨ Resumir Histórico do Chamado (Inteligência Artificial)"):
                        historico = st.text_area("Cole aqui os assentamentos do cliente para análise rápida:", height=100)
                        if st.button("Mastigar Histórico"):
                            if ia_ativa and historico:
                                with st.spinner("Processando os dados..."):
                                    try:
                                        resp = gerar_conteudo_gemini(f"Analise este histórico de atendimento e devolva os 3 pontos mais importantes (causa, situação atual e o que o cliente quer). Seja extremamente resumido:\n\n{historico}")
                                        st.info(resp.text)
                                    except Exception as e: st.error(f"Erro na IA: {e}")
                            elif not ia_ativa: st.error("IA desligada. Verifique a chave de API.")
                            else: st.warning("Cole o texto primeiro.")

                    st.write("---")
                    if 'confirmar' not in st.session_state: st.session_state['confirmar'] = False
                    
                    if not st.session_state['confirmar']:
                        if st.button("✅ FINALIZAR", type="primary"): st.session_state['confirmar'] = True; st.rerun()
                    else:
                        st.warning("Confirma a conclusão?")
                        cy, cn = st.columns(2)
                        if cy.button("👍 SIM"):
                            idx_linha = int(meu_chamado.index[0]) + 2
                            aba_chamados.batch_update([
                                {"range": rowcol_to_a1(idx_linha, COL_STATUS), "values": [["Concluido"]]},
                                {"range": rowcol_to_a1(idx_linha, COL_FIM), "values": [[hora_texto()]]},
                            ])
                            liberar_reserva(aba_chamados, idx_linha)
                            registrar_log(usuario, f"Finalizou {num}")
                            verificar_meta_baloes(usuario, ranking_global, META_DIARIA)
                            st.session_state['confirmar'] = False
                            invalidar_cache_base("qualitor"); st.rerun()
                        if cn.button("❌ NÃO"): st.session_state['confirmar'] = False; st.rerun()
                else:
                    fila = df[(df['Status'] == 'Pendente') & (df['Responsavel'] == "")].copy()
                    if "Todas" not in minhas_etapas and "todas" not in [e.lower() for e in minhas_etapas]:
                        fila = fila[fila['Etapa'].astype(str).isin(minhas_etapas)]
                    
                    qtd = len(fila)
                    area_fila, area_meta = st.columns([1.35, 1], gap="medium")
                    with area_fila:
                        with st.container(border=True, key="frg-queue-qualitor"):
                            renderizar_indicador("Sua fila Qualitor", qtd, "Chamados disponíveis para suas etapas", compacto=True)
                            if st.button("🔄 Atualizar Fila"): invalidar_cache_base("qualitor"); st.rerun()
                            if qtd > 0:
                                # ✅ O ÚNICO E VERDADEIRO BOTÃO!
                                if st.button("📥 PEGAR PRÓXIMO", type="primary", width="content"):
                                    if 'SLA' in fila.columns:
                                        fila['Peso_SLA'] = fila['SLA'].astype(str).apply(lambda x: 1 if 'fora' in x.lower() else 2)
                                        fila = fila.sort_values(by='Peso_SLA', kind='stable')
                                
                                    item = fila.iloc[0]
                                    idx_linha = int(item.name) + 2 
                                    num_chamado = str(item.get('Dados', '')).replace('.0', '') # Puxa o número do chamado
                                    ok, mensagem = reservar_atendimento(aba_chamados, idx_linha, usuario, "Em Andamento", COL_STATUS, COL_RESP, COL_INICIO)
                                    if ok:
                                        registrar_log(usuario, f"Pegou chamado Qualitor ({num_chamado})")
                                        invalidar_cache_base("qualitor"); st.rerun()
                                    else:
                                        invalidar_cache_base("qualitor"); st.error(mensagem)
                            else: 
                                st.info("Nenhum chamado. Clique em 'Atualizar Fila' para checar.")
                    with area_meta:
                        with st.container(border=True, key="frg-goal-qualitor"):
                            renderizar_meta(qtd_minha, META_DIARIA)
                    with st.form("frg-busca-qualitor", border=True):
                        st.markdown("**Buscar chamado específico**")
                        c_busca, c_btn = st.columns([4, 1.5], vertical_alignment="bottom")
                        pedido_busca = c_busca.text_input("Nº do Chamado:", placeholder="Ex: 384405")
                        if c_btn.form_submit_button("Buscar e puxar", width="stretch"):
                            if pedido_busca.strip():
                                alvo = fila[fila['Dados'].astype(str).str.contains(pedido_busca.strip(), case=False, na=False)]
                                if not alvo.empty:
                                    item = alvo.iloc[0]; idx_linha = int(item.name) + 2
                                    ok, mensagem = reservar_atendimento(aba_atual, idx_linha, usuario, "Em Andamento", COL_STATUS, COL_RESP, COL_INICIO)
                                    if ok:
                                        registrar_log(usuario, f"Busca Ativa Qualitor: Pegou {pedido_busca}")
                                        st.success("Encontrado! Puxando para sua tela..."); invalidar_cache_base("qualitor"); st.rerun()
                                    else:
                                        invalidar_cache_base("qualitor"); st.error(mensagem)
                                else: st.error("Chamado não encontrado na fila permitida ou já em atendimento.")
                            else: st.warning("Digite um número de chamado.")

                        # --- HISTÓRICO DE CHAMADOS (QUALITOR) ---
            # --- HISTÓRICO DE CHAMADOS (QUALITOR) ---
            st.write("---")
            if not df.empty:
                hist = df[(df['Status']=='Concluido') & (df['Responsavel']==usuario)].copy()
                if not hist.empty and 'Data_Conclusao' in hist.columns:
                    hoje = data_hoje()
                    hist_hoje = hist[hist['Data_Conclusao'].astype(str).str.contains(hoje)].copy()
                    qtd_hoje = len(hist_hoje)
                    st.subheader(f"✅ Seus Concluídos Hoje: **{qtd_hoje}**")
                    
                    if qtd_hoje > 0:
                        hist_hoje['Tempo_Gasto'] = hist_hoje.apply(lambda row: calcular_duracao_str(row.get('Inicio', ''), row.get('Data_Conclusao', '')), axis=1)
                        
                        st.markdown("*(Para reabrir ou consultar um chamado, clique na célula do número, faça Ctrl+C e cole na barra de busca do Qualitor)*")
                        
                        hist_hoje = hist_hoje.rename(columns={'Data_Conclusao': 'Horário', 'Dados': 'Nº Chamado'})
                        cols_show = ['Nº Chamado', 'Etapa', 'SLA', 'Tempo_Gasto', 'Horário'] if 'SLA' in hist_hoje.columns else ['Nº Chamado', 'Etapa', 'Tempo_Gasto', 'Horário']
                        
                        st.dataframe(hist_hoje[cols_show].tail(15), hide_index=True, width="stretch")
                else:
                    st.caption("Nenhum chamado concluído por você hoje, ainda. Vamos lá!")

    # ===================================================
    # 🧙‍♂️ GAVETA DO ORÁCULO E TRANSPORTADORAS (PARA TODOS)
    # ===================================================
    if usuario != "TV" and not modo_gerente:
        st.subheader("Apoio durante o atendimento")
        with st.expander("🧙‍♂️ Oráculo Frigelar"):
            pergunta = st.text_input("O que você precisa saber?")
            if st.button("✨ Perguntar"):
                if ia_ativa:
                    try:
                        with open("regras_operacao.txt", "r", encoding="utf-8") as f:
                            resposta = gerar_conteudo_gemini(f"Seja o Oráculo do SAC Frigelar. Responda baseado no manual:\n{f.read()}\n\nPergunta: {pergunta}")
                            st.info(resposta.text)
                    except Exception as e: st.error(f"Erro: {e}")
                else: st.error(f"IA não configurada.")
                
        with st.expander("🚚 Contatos de transportadoras"):
            df_transp = carregar_agenda_transp()
            if not df_transp.empty and 'Transportadora' in df_transp.columns:
                lista_t = [str(t) for t in df_transp['Transportadora'].dropna().unique() if str(t).strip() != '']
                escolha_t = st.selectbox("Selecione a Transportadora:", [""] + sorted(lista_t))
                if escolha_t:
                    dados_t = df_transp[df_transp['Transportadora'] == escolha_t].iloc[0]
                    c_t, c_l = st.columns(2)
                    with c_t:
                        st.caption("E-mail Transportadora (Clique para copiar):")
                        st.code(dados_t.get('Email_Transp', 'Não cadastrado'), language="text") 
                    with c_l:
                        st.caption("E-mail Logística (Clique para copiar):")
                        st.code(dados_t.get('Email_Logistica', 'Não cadastrado'), language="text")
            else: st.info("Aba 'Transportadoras' não encontrada ou vazia.")


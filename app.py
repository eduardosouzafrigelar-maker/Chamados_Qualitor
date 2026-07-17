import streamlit as st
import pandas as pd
import gspread
from datetime import datetime, timedelta
import time
import pytz
import os
import requests
import streamlit.components.v1 as components
import google.generativeai as genai
from streamlit_autorefresh import st_autorefresh
from fpdf import FPDF
import unicodedata
import base64
import numpy as np
import re

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(page_title="Esteira Qualitor", page_icon="🎫", layout="wide")

# --- 👑 ADMINISTRAÇÃO E SQUADS ---
ADMINS = ["Eduardo", "EduardoSouza", "Gestor", "Lopes", "eduardosouza", "biancamoura", "andreacastro"] 

SQUAD_AZIX = ["charleneoliveira", "brunasouza2", "viniciosmarques2"] 
SQUAD_MKTP = ["vitoriabraga", "fabiolapereira"] 

# --- META DIÁRIA E CELEBRAÇÃO ---
META_DIARIA = 50

def verificar_meta_baloes(usuario_atual, df_ranking, meta):
    feitos_hoje = 0
    if not df_ranking.empty:
        minha_linha = df_ranking[df_ranking['Nome'] == usuario_atual]
        if not minha_linha.empty:
            feitos_hoje = int(minha_linha.iloc[0]['Qtd'])
    
    feitos_agora = feitos_hoje + 1
    if feitos_agora in [10, 25, meta, 75, 100]:
        st.session_state['soltar_baloes'] = True

# --- 🧠 CONFIGURAÇÃO DA IA (ORÁCULO E RESUMIDOR) ---
try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    modelo_escolhido = None
    for m in genai.list_models():
        if 'generateContent' in m.supported_generation_methods:
            if 'flash' in m.name: 
                modelo_escolhido = m.name
                break
    if not modelo_escolhido:
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                modelo_escolhido = m.name
                break
    if modelo_escolhido and modelo_escolhido.startswith("models/"):
        modelo_escolhido = modelo_escolhido.replace("models/", "")
        
    modelo_oraculo = genai.GenerativeModel(modelo_escolhido)
    ia_ativa = True
except Exception as e:
    modelo_oraculo = None
    ia_ativa = False
    erro_ia = str(e)

# --- 🚨 ALERTA MICROSOFT TEAMS ---
def alertar_teams(mensagem):
    webhook_url = "https://frigelar.webhook.office.com/webhookb2/ec98f756-9855-46a4-a0c4-084062e87994@d8d0f357-174f-48d7-b3b2-a5a630b0cd99/IncomingWebhook/4282a43bc29f475d9d1ca3629f01fcd6/5cd3c896-0830-48e2-9541-84f9563e933b/V2CUlXhnGitZt59misdhM4o9QEsdDxbpLgnT7PUUALYJc1"
    if webhook_url != "https://teams.microsoft.com/l/chat/48:notes/conversations?context=%7B%22contextType%22%3A%22chat%22%7D":
        try: requests.post(webhook_url, json={"text": mensagem})
        except: pass

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
                return "🚨 ERRO: Credenciais não encontradas.", None, None, None, None, None
        
        erro_real = ""
        for tentativa in range(10):
            try:
                sh = client.open("Chamados_Qualitor") 
                abas = sh.worksheets()
                if len(abas) >= 2:
                    aba_chamados = abas[0] if len(abas) > 0 else None
                    aba_users = abas[1] if len(abas) > 1 else None
                    aba_logs = abas[2] if len(abas) > 2 else None
                    aba_transp = abas[3] if len(abas) > 3 else None
                    aba_azix = abas[4] if len(abas) > 4 else None 
                    return sh, aba_chamados, aba_users, aba_logs, aba_transp, aba_azix
                else: erro_real = "A planilha tem menos de 2 abas visíveis."
            except Exception as e:
                erro_real = str(e)
                time.sleep(2 + tentativa)
        return f"Falha após 10 tentativas. Erro: {erro_real}", None, None, None, None, None
    except Exception as e:
        return f"Erro Crítico: {str(e)}", None, None, None, None, None

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
sh, aba_chamados, aba_users, aba_logs, aba_transp, aba_azix = conectar_e_abrir_abas()
if isinstance(sh, str): st.error(f"Erro de conexão: {sh}"); st.stop()
if aba_chamados is None: st.error("Erro ao carregar abas principais."); st.stop()

def registrar_log(usuario, acao):
    try: aba_logs.append_row([usuario, acao, hora_texto()])
    except: pass

@st.cache_data(ttl=15)
def carregar_dados_chamados():
    try:
        df = pd.DataFrame(aba_chamados.get_all_records())
        if 'Dados' in df.columns: df['Dados'] = df['Dados'].astype(str).str.replace(r'\.0$', '', regex=True)
        if 'Status' in df.columns: df['Status'] = df['Status'].astype(str).str.strip()
        return df
    except: return pd.DataFrame()

@st.cache_data(ttl=15)
def carregar_dados_azix():
    if aba_azix is None: return pd.DataFrame()
    try:
        df = pd.DataFrame(aba_azix.get_all_records())
        if 'Nº Pedido venda' in df.columns: df['Nº Pedido venda'] = df['Nº Pedido venda'].astype(str).str.replace(r'\.0$', '', regex=True)
        if 'Status' in df.columns: df['Status'] = df['Status'].astype(str).str.strip()
        return df
    except: return pd.DataFrame()

@st.cache_data(ttl=30)
def carregar_status_equipe():
    try:
        dados = aba_users.get_all_values()
        if not dados: return pd.DataFrame()
        df = pd.DataFrame(dados[1:], columns=dados[0])
        return df.loc[:, df.columns != ''] 
    except: return pd.DataFrame()

@st.cache_data(ttl=3600) 
def carregar_agenda_transp():
    if aba_transp is None: return pd.DataFrame()
    try: return pd.DataFrame(aba_transp.get_all_records())
    except: return pd.DataFrame()

@st.cache_data(ttl=15)
def carregar_logs_dia():
    if aba_logs is None: return pd.DataFrame()
    try:
        dados = aba_logs.get_all_values()
        if not dados: return pd.DataFrame()
        if dados[0][0].lower() in ['usuario', 'nome', 'operador']: return pd.DataFrame(dados[1:], columns=["Usuario", "Acao", "DataHora"])
        else: return pd.DataFrame(dados, columns=["Usuario", "Acao", "DataHora"])
    except: return pd.DataFrame()

def ler_mural():
    try:
        with open("mural.txt", "r", encoding="utf-8") as f: return f.read().strip()
    except: return ""

def salvar_mural(texto):
    with open("mural.txt", "w", encoding="utf-8") as f: f.write(texto)

def is_marketplace(texto):
    palavras_chave = ['MAGAZINE', 'MERCADO', 'B2W', 'AMAZON', 'SHOPEE', 'CARREFOUR']
    return any(k in str(texto).upper() for k in palavras_chave)

# --- RANKING GLOBAL ---
df_logs_global = carregar_logs_dia()
ranking_global = pd.DataFrame()
if not df_logs_global.empty:
    hoje = data_hoje()
    logs_hoje = df_logs_global[df_logs_global['DataHora'].astype(str).str.contains(hoje)]
    feitos_logs = logs_hoje[logs_hoje['Acao'].astype(str).str.contains("Finalizou|Encerrada", case=False)]
    if not feitos_logs.empty:
        ranking_global = feitos_logs['Usuario'].value_counts().reset_index()
        ranking_global.columns = ['Nome', 'Qtd']

if 'soltar_baloes' in st.session_state and st.session_state['soltar_baloes']:
    st.balloons(); st.session_state['soltar_baloes'] = False

if 'tema_escolhido' not in st.session_state: st.session_state['tema_escolhido'] = "Padrão"

# ===================================================
# 🎫 TELA DE LOGIN CORPORATIVA BLINDADA
# ===================================================
def render_corporate_login():
    st.markdown("""
    <style>
        .stApp { background: linear-gradient(135deg, #0f1c3a 0%, #173775 100%) !important; }
        [data-testid="stSidebar"], [data-testid="stHeader"] { display: none !important; }
        .main .block-container { max-width: 1200px !important; padding-top: 8vh !important; }
        [data-testid="stForm"] { background-color: #ffffff !important; padding: 50px 40px !important; border-radius: 15px !important; box-shadow: 0 15px 35px rgba(0,0,0,0.5) !important; border: none !important; width: 100% !important; max-width: 480px !important; margin: 0 auto !important; }
        [data-testid="stForm"] div[data-baseweb="input"] { background-color: #ffffff !important; border: 2px solid #e2e8f0 !important; border-radius: 8px !important; transition: all 0.3s ease !important; height: 55px !important; }
        [data-testid="stForm"] input { color: #0f172a !important; -webkit-text-fill-color: #0f172a !important; font-size: 1.1em !important; height: 55px !important; line-height: 55px !important; padding-top: 0px !important; padding-bottom: 0px !important; background-color: transparent !important; }
        [data-testid="stForm"] input::placeholder { color: #a0aec0 !important; -webkit-text-fill-color: #a0aec0 !important; opacity: 1 !important; line-height: 55px !important; }
        [data-testid="stForm"] input[type="text"] { background-image: url("data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 448 512'%3E%3Cpath fill='%23a0aec0' d='M224 256A128 128 0 1 0 224 0a128 128 0 1 0 0 256zm-45.7 48C79.8 304 0 383.8 0 482.3C0 498.7 13.3 512 29.7 512l388.6 0c16.4 0 29.7-13.3 29.7-29.7C448 383.8 368.2 304 269.7 304l-91.4 0z'/%3E%3C/svg%3E") !important; background-repeat: no-repeat !important; background-position: 15px 50% !important; background-size: 18px !important; padding-left: 50px !important; }
        [data-testid="stForm"] input[type="password"] { background-image: url("data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 448 512'%3E%3Cpath fill='%23a0aec0' d='M144 144v48H304V144c0-44.2-35.8-80-80-80s-80 35.8-80 80zM80 192V144C80 64.5 144.5 0 224 0s144 64.5 144 144v48h16c35.3 0 64 28.7 64 64V448c0 35.3-28.7 64-64 64H64c-35.3 0-64-28.7-64-64V256c0-35.3 28.7-64 64-64H80z'/%3E%3C/svg%3E") !important; background-repeat: no-repeat !important; background-position: 15px 50% !important; background-size: 16px !important; padding-left: 50px !important; }
        [data-testid="stForm"] div[data-baseweb="input"]:focus-within { border-color: #38bdf8 !important; box-shadow: 0 0 0 1px #38bdf8 !important; }
        [data-testid="stFormSubmitButton"] button { background-color: #173775 !important; color: #ffffff !important; border: none !important; border-radius: 30px !important; height: 55px !important; font-weight: bold !important; font-size: 1.2em !important; margin-top: 20px !important; width: 100% !important; transition: all 0.3s ease !important; }
        [data-testid="stFormSubmitButton"] button:hover { background-color: #38bdf8 !important; box-shadow: 0 5px 15px rgba(56,189,248,0.4) !important; color: #ffffff !important; }
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
        if st.button("🔄 Recarregar Nomes"): st.cache_data.clear(); st.rerun()

    render_corporate_login()
    def get_image_base64(caminho_imagem):
        try:
            with open(caminho_imagem, "rb") as img_file: return base64.b64encode(img_file.read()).decode()
        except: return "" 
    logo_b64 = get_image_base64("logo_frigelar.png")
    
    c1, c2, c3 = st.columns([1, 1.5, 1]) 
    with c2:
        with st.form("login_form", clear_on_submit=False):
            img_src = f"data:image/png;base64,{logo_b64}" if logo_b64 else "https://raichu-uploads.s3.amazonaws.com/logo_frigelar_QERmNQ.png"
            st.markdown(f'''
            <div align="center" style="margin-bottom: 25px;">
                <img src="{img_src}" style="width: 200px; display: block; margin: 0 auto; transform: translateX(-15px);">
                <h1 style="color: #173775; font-size: 2.4em; margin: 15px 0 5px 0; font-weight: 800;">BEM-VINDO</h1>
                <h2 style="color: #718096; font-size: 1.1em; margin: 0; font-weight: 400;">Sistema de Chamados</h2>
            </div>
            ''', unsafe_allow_html=True)
            user_digitado = st.text_input("Utilizador", placeholder="Coloque o seu usuário", label_visibility="collapsed")
            senha_digitada = st.text_input("Senha", type="password", placeholder="Coloque a sua senha", label_visibility="collapsed")
            submitted = st.form_submit_button("ENTRAR", use_container_width=True)
            if submitted:
                if user_digitado in lista_nomes and str(senha_digitada) == str(senhas.get(user_digitado, "")):
                    st.session_state['usuario'] = user_digitado
                    st.session_state['tamanho_fila_anterior'] = 0 
                    registrar_log(user_digitado, "LOGIN") 
                    try:
                        idx = df_equipe.index[df_equipe['Colaboradores'] == user_digitado].tolist()[0] + 2
                        aba_users.update_cell(idx, 3, "Disponivel")
                        st.cache_data.clear() 
                    except: pass
                    st.rerun()
                else: st.error("❌ Login não encontrado ou senha incorreta.")

# ===================================================
# SISTEMA LOGADO
# ===================================================
else:
    usuario = st.session_state['usuario']
    
    # 🧠 ROTEAMENTO DE BASES
    df_qualitor = carregar_dados_chamados()
    df_azix_data = carregar_dados_azix()
    df_equipe = carregar_status_equipe()
    
    # PREPARA O SLA DO AZIX
    if not df_azix_data.empty:
        if 'Data_Entrada' not in df_azix_data.columns:
            df_azix_data['Data_Entrada'] = data_hoje()
        df_azix_data['Status_SLA'] = df_azix_data['Data_Entrada'].apply(calcular_sla_bizdays)

    if usuario in SQUAD_AZIX or usuario in SQUAD_MKTP:
        df = df_azix_data
        aba_atual = aba_azix
    else:
        df = df_qualitor
        aba_atual = aba_chamados

    # 📍 O GPS DE COLUNAS (A CORREÇÃO DO ERRO ENTRA AQUI!)
    if not df.empty:
        cols_planilha = df.columns.tolist()
        COL_STATUS = cols_planilha.index("Status") + 1 if "Status" in cols_planilha else 3
        COL_RESP = cols_planilha.index("Responsavel") + 1 if "Responsavel" in cols_planilha else 5
        COL_INICIO = cols_planilha.index("Inicio") + 1 if "Inicio" in cols_planilha else 6
        COL_FIM = cols_planilha.index("Data_Conclusao") + 1 if "Data_Conclusao" in cols_planilha else 7
    else:
        COL_STATUS, COL_RESP, COL_INICIO, COL_FIM = 3, 5, 6, 7

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

    # --- BARRA LATERAL ---
    with st.sidebar:
        st.header(f"👤 {saudacao}, {usuario}!")
        modo_gerente = False
        if usuario in ADMINS:
            st.success("👑 Modo Gestor Liberado")
            modo_gerente = st.toggle("Painel de Gestão", value=False)
            st.divider()
            
        st.info(f"Status Atual: **{status_real}**")
        c1, c2 = st.columns(2)
        if c1.button("🟢 Online"):
            if linha_planilha: aba_users.update_cell(linha_planilha, 3, "Disponivel"); registrar_log(usuario, "Ficou Disponivel"); st.cache_data.clear(); st.rerun()
        if c2.button("☕ Pausa"):
            if linha_planilha: aba_users.update_cell(linha_planilha, 3, "Pausa"); registrar_log(usuario, "Entrou em Pausa"); st.cache_data.clear(); st.rerun()
        if st.button("🚽 Banheiro"):
            if linha_planilha: aba_users.update_cell(linha_planilha, 3, "Banheiro"); registrar_log(usuario, "Foi ao Banheiro"); st.cache_data.clear(); st.rerun()
        
        st.divider()
        st.subheader("🏆 Seu Desempenho Hoje")
        
        # GAMIFICAÇÃO: Barra de Progresso
        qtd_minha = 0
        if not ranking_global.empty:
            minha_posicao = ranking_global.index[ranking_global['Nome'] == usuario].tolist()
            if minha_posicao:
                qtd_minha = ranking_global.iloc[minha_posicao[0]]['Qtd']
        
        pct = min(qtd_minha / META_DIARIA, 1.0) * 100
        cor_barra = "#ef4444" if pct < 50 else "#f59e0b" if pct < 100 else "#22c55e"
        
        st.markdown(f"""
        <div style="width: 100%; background-color: #e2e8f0; border-radius: 10px; margin-top: 5px;">
            <div style="width: {pct}%; background-color: {cor_barra}; height: 18px; border-radius: 10px; transition: 0.5s;"></div>
        </div>
        <p style="text-align: center; font-size: 13px; color: gray; margin-top: 5px;"><b>{qtd_minha}</b> de <b>{META_DIARIA}</b> chamados encerrados neste sistema</p>
        """, unsafe_allow_html=True)
        
        if not ranking_global.empty and minha_posicao:
            pos_real = minha_posicao[0] + 1
            if pos_real == 1: st.success(f"🥇 1º Lugar na equipe!")
            elif pos_real == 2: st.info(f"🥈 2º Lugar na equipe!")
            else: st.markdown(f"📍 **Sua posição no ranking:** {pos_real}º Lugar")
        else: st.caption("A corrida de hoje ainda não começou!")

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
            if st.button("Salvar Nova Senha", use_container_width=True):
                if nova_senha == confirma_senha and len(nova_senha) >= 4:
                    try:
                        cols_users = df_equipe.columns.tolist()
                        if "Senha" in cols_users:
                            col_senha_idx = cols_users.index("Senha") + 1
                            aba_users.update_cell(linha_planilha, col_senha_idx, nova_senha)
                            registrar_log(usuario, "Mudou a própria senha")
                            st.success("✅ Senha atualizada! Use no próximo login.")
                            st.cache_data.clear()
                        else: st.error("Erro: Coluna 'Senha' não encontrada.")
                    except Exception as e: st.error(f"Erro ao mudar senha: {e}")
                else: st.warning("⚠️ Senhas não batem ou são curtas (mín. 4).")
                
        st.divider()
        if st.button("Sair (Logout)"): registrar_log(usuario, "LOGOUT"); del st.session_state['usuario']; st.rerun()

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
        azix_hoje_conc = len(logs_hoje[logs_hoje['Acao'].astype(str).str.contains("Concluiu Azix", case=False)]) if not df_logs_global.empty else 0
        azix_hoje_avan = len(logs_hoje[logs_hoje['Acao'].astype(str).str.contains("Azix para Mktp", case=False)]) if not df_logs_global.empty else 0
        
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
            
        time.sleep(15); st.cache_data.clear(); st.rerun()

    # ===================================================
    # 👑 VISÃO DO GESTOR (ADMIN)
    # ===================================================
    elif modo_gerente:
        st.title("📊 Painel de Controle - Gestão")
        st.caption(f"Última atualização: {hora_texto()}")
        if st.button("🔄 Atualizar Tudo (Limpar Cache)"): st.cache_data.clear(); st.rerun()
        
        # --- MÁQUINA DO TEMPO (FILTRO DE PERÍODO NO FORMATO BR) ---
        st.write("---")
        st.subheader("📅 Máquina do Tempo (Filtro de Período dos Resultados)")
        c_dt1, c_dt2 = st.columns(2)
        hoje_date = hora_brasil().date()
        data_inicio = c_dt1.date_input("Data Inicial", hoje_date - timedelta(days=7), format="DD/MM/YYYY")
        data_fim = c_dt2.date_input("Data Final", hoje_date, format="DD/MM/YYYY")
        
        df_logs = carregar_logs_dia()
        ranking_periodo = pd.DataFrame()
        df_logs_periodo = pd.DataFrame()
        
        azix_concluidos_periodo = 0
        azix_avancados_periodo = 0
        azix_devolvidos_periodo = 0
        importados_azix = 0
        
        if not df_logs.empty:
            df_logs['DataReal'] = pd.to_datetime(df_logs['DataHora'].str.split(' ').str[0], format="%d/%m/%Y", errors='coerce').dt.date
            df_logs_periodo = df_logs[(df_logs['DataReal'] >= data_inicio) & (df_logs['DataReal'] <= data_fim)].copy()
            
            feitos_periodo = df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Finalizou|Encerrada|Concluiu Azix", case=False)]
            if not feitos_periodo.empty:
                ranking_periodo = feitos_periodo['Usuario'].value_counts().reset_index()
                ranking_periodo.columns = ['Nome', 'Qtd']
                
            # Ler a história do Azix na Máquina do Tempo (Agora contando devoluções!)
            azix_concluidos_periodo = len(df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Concluiu Azix", case=False)])
            azix_avancados_periodo = len(df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Azix para Mktp", case=False)])
            azix_devolvidos_periodo = len(df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Devolveu Azix", case=False)])
            
            # Soma os importados no período
            logs_importacao = df_logs_periodo[df_logs_periodo['Acao'].astype(str).str.contains("Adicionou", case=False)]
            for acao in logs_importacao['Acao']:
                nums = re.findall(r'\d+', str(acao))
                if nums: importados_azix += int(nums[0])

        # --- DASHBOARDS GERENCIAIS COM CARDS ---
        st.write("---")
        st.subheader("📈 Análise de Produtividade e Filas (Tempo Real e Período)")
        
        pend_q = len(df_qualitor[df_qualitor['Status'] == 'Pendente']) if not df_qualitor.empty else 0
        fora_sla_q = len(df_qualitor[(df_qualitor['Status'] == 'Pendente') & (df_qualitor['SLA'].astype(str).str.contains('Prioridade 1|fora', case=False, na=False))]) if not df_qualitor.empty and 'SLA' in df_qualitor.columns else 0
        
        pend_a = len(df_azix_data[df_azix_data['Status'].isin(['Pendente', 'Pendente - Retorno', 'Aguardando Reivindicação'])]) if not df_azix_data.empty else 0
        fora_sla_a = len(df_azix_data[(df_azix_data['Status'].isin(['Pendente', 'Pendente - Retorno', 'Aguardando Reivindicação'])) & (df_azix_data['Status_SLA'].astype(str).str.contains('Atrasado|Vence Hoje', case=False, na=False))]) if not df_azix_data.empty and 'Status_SLA' in df_azix_data.columns else 0
        
        col1, col2, col3 = st.columns(3)
        col1.markdown(f"""
        <div style='background-color: #f0f9ff; padding: 20px; border-radius: 10px; border-left: 5px solid #0284c7; margin-bottom: 20px;'>
            <h4 style='color: #0369a1; margin-top:0;'>📦 Qualitor (SAC)</h4>
            <h2>{pend_q} <span style='font-size: 0.5em; font-weight: normal; color: gray;'>Pendentes</span></h2>
            <p style='color: #dc2626; margin-bottom:0;'>🔥 {fora_sla_q} Atrasados/Críticos</p>
        </div>
        """, unsafe_allow_html=True)
        
        col2.markdown(f"""
        <div style='background-color: #fff7ed; padding: 20px; border-radius: 10px; border-left: 5px solid #d97706; margin-bottom: 20px;'>
            <h4 style='color: #b45309; margin-top:0;'>🔶 Azix / Marketplace</h4>
            <h2>{pend_a} <span style='font-size: 0.5em; font-weight: normal; color: gray;'>Pendentes Atuais</span></h2>
            <p style='color: #dc2626; font-size: 0.9em; margin-bottom: 10px;'>🔥 {fora_sla_a} Atrasados/Críticos</p>
            <div style='border-top: 1px solid #fde68a; padding-top: 10px; margin-top: 10px;'>
                <p style='color: #92400e; margin:0; font-size: 0.85em;'><b>No Período Filtrado acima:</b></p>
                <p style='color: #b45309; margin:0; font-size: 0.85em;'>📥 Importados p/ fila: <b>{importados_azix}</b></p>
                <p style='color: #16a34a; margin:0; font-size: 0.85em;'>✅ Encerrados: <b>{azix_concluidos_periodo}</b></p>
                <p style='color: #0284c7; margin:0; font-size: 0.85em;'>⏭️ Avançaram Etapa: <b>{azix_avancados_periodo}</b></p>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        total_feitos_periodo = ranking_periodo['Qtd'].sum() if not ranking_periodo.empty else 0
        col3.markdown(f"""
        <div style='background-color: #f0fdf4; padding: 20px; border-radius: 10px; border-left: 5px solid #16a34a; margin-bottom: 20px;'>
            <h4 style='color: #15803d; margin-top:0;'>✅ Produtividade (Período)</h4>
            <h2>{total_feitos_periodo} <span style='font-size: 0.5em; font-weight: normal; color: gray;'>Concluídos</span></h2>
            <p style='color: #16a34a; margin-bottom:0;'>De {data_inicio.strftime('%d/%m')} até {data_fim.strftime('%d/%m')}</p>
        </div>
        """, unsafe_allow_html=True)
        
        c_graf1, c_graf2 = st.columns(2)
        with c_graf1:
            st.markdown("**Status Atual da Operação (Qualitor + Azix)**")
            if not df_qualitor.empty and not df_azix_data.empty:
                status_comb = pd.concat([df_qualitor['Status'], df_azix_data['Status']])
                st.bar_chart(status_comb.value_counts(), use_container_width=True)
            else: st.info("Sem dados")
        with c_graf2:
            st.markdown(f"**Top Produtividade ({data_inicio.strftime('%d/%m')} a {data_fim.strftime('%d/%m')})**")
            if not ranking_periodo.empty: st.bar_chart(ranking_periodo.set_index('Nome'), use_container_width=True)
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
                    st.bar_chart(df_pendentes_qualitor['Etapa'].value_counts(), use_container_width=True)
                with c_etapa2:
                    st.dataframe(contagem_etapas, hide_index=True, use_container_width=True)
            else:
                st.success("Não há chamados pendentes na fila do Qualitor no momento.")
        else:
            st.info("A base Qualitor está vazia ou sem a coluna 'Etapa'.")

        # --- HEADCOUNT E DIMENSIONAMENTO (WFM) ---
        st.write("---")
        st.subheader("👥 Força de Trabalho e Dimensionamento (WFM)")
        
        # 1. Calcular Headcount de Hoje pelos Logs
        hoje_str = data_hoje()
        logs_do_dia = df_logs[df_logs['DataHora'].astype(str).str.contains(hoje_str)] if not df_logs.empty else pd.DataFrame()
        
        qtd_azix_hoje = 0
        qtd_qualitor_hoje = 0
        nomes_azix = ""
        nomes_qualitor = ""
        
        if not logs_do_dia.empty:
            usuarios_logados = logs_do_dia['Usuario'].unique()
            # Filtra os chefes e a TV para não "sujar" o headcount operacional
            ops_reais = [u for u in usuarios_logados if u not in ADMINS and u != "TV"]
            
            # Conta quem é de qual Squad
            ops_azix = [u for u in ops_reais if u in SQUAD_AZIX or u in SQUAD_MKTP]
            ops_qualitor = [u for u in ops_reais if u not in SQUAD_AZIX and u not in SQUAD_MKTP]
            
            qtd_azix_hoje = len(ops_azix)
            qtd_qualitor_hoje = len(ops_qualitor)
            
            # Prepara a lista de nomes formatada separada por vírgula
            nomes_azix = ", ".join(ops_azix) if qtd_azix_hoje > 0 else "Nenhum"
            nomes_qualitor = ", ".join(ops_qualitor) if qtd_qualitor_hoje > 0 else "Nenhum"

        c_wfm1, c_wfm2, c_wfm3 = st.columns(3)
        
        with c_wfm1:
            st.markdown(f"""
            <div style='background-color: #f0f9ff; padding: 15px; border-radius: 8px; border-left: 5px solid #0284c7; height: 100%;'>
                <p style='margin:0; color: #0369a1; font-size: 0.9em;'>Operadores Qualitor (Atuaram Hoje):</p>
                <h2 style='margin:0; color: #0f172a;'>👨‍💻 {qtd_qualitor_hoje}</h2>
                <p style='margin-top:10px; font-size: 0.75em; color: #475569;'><b>Nomes:</b> {nomes_qualitor}</p>
            </div>
            """, unsafe_allow_html=True)
        
        with c_wfm2:
            st.markdown(f"""
            <div style='background-color: #fff7ed; padding: 15px; border-radius: 8px; border-left: 5px solid #d97706; height: 100%;'>
                <p style='margin:0; color: #b45309; font-size: 0.9em;'>Operadores Azix/Mktp (Atuaram Hoje):</p>
                <h2 style='margin:0; color: #0f172a;'>👨‍💻 {qtd_azix_hoje}</h2>
                <p style='margin-top:10px; font-size: 0.75em; color: #475569;'><b>Nomes:</b> {nomes_azix}</p>
            </div>
            """, unsafe_allow_html=True)
        
        with c_wfm3:
            st.markdown("<p style='margin:0; color: #475569; font-weight: bold;'>⏱️ Calculadora Qualitor</p>", unsafe_allow_html=True)
            col_tma, col_hora = st.columns(2)
            tma_minutos = col_tma.number_input("TMA (min):", min_value=1, value=8)
            hora_fim = col_hora.time_input("Fim Turno:", value=datetime.strptime("18:00", "%H:%M").time())
            
            agora = hora_brasil()
            fim_expediente_dt = agora.replace(hour=hora_fim.hour, minute=hora_fim.minute, second=0, microsecond=0)
            minutos_restantes = int((fim_expediente_dt - agora).total_seconds() / 60)
            
            if minutos_restantes > 0 and pend_q > 0:
                pessoas_necessarias = np.ceil((pend_q * tma_minutos) / minutos_restantes)
                st.info(f"**Ideal:** {int(pessoas_necessarias)} pessoas para zerar a fila hoje.")
                
                # O Cérebro Mágico: Compara quem você tem hoje com quem você precisa!
                if qtd_qualitor_hoje < pessoas_necessarias:
                    st.error(f"⚠️ Risco de atraso! Faltam {int(pessoas_necessarias - qtd_qualitor_hoje)} operadores.")
                else:
                    st.success("✅ Equipa perfeitamente dimensionada para a fila!")
            elif pend_q == 0:
                st.success("🎉 Fila Qualitor zerada!")
            else:
                st.error("⏰ Expediente encerrado.")

        
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
            
            # 2. Quantos trataram (Soma de todos que já ganharam um status diferente de vazio/Não Tratado)
            trataram_total = aceitaram + reprovaram + aguardando
            
            # Cards na tela
            c_v1, c_v2, c_v3, c_v4, c_v5 = st.columns(5)
            c_v1.metric("📥 Base Total (Entraram)", total_entraram)
            c_v2.metric("⚙️ Já Tratados", trataram_total)
            c_v3.metric("✅ Aceitaram", aceitaram)
            c_v4.metric("❌ Reprovaram", reprovaram)
            c_v5.metric("⏳ Aguardando", aguardando)
            
            # Gráfico de Validação
            if trataram_total > 0:
                dados_grafico_val = pd.DataFrame({
                    'Status': ['Aceitou', 'Negou', 'Aguardando'],
                    'Quantidade': [aceitaram, reprovaram, aguardando]
                }).set_index('Status')
                st.bar_chart(dados_grafico_val, use_container_width=True, color="#d97706")
        else:
            st.info("Aguardando as primeiras validações da equipa ou a coluna 'Validacao_Receita' ainda não foi lida na base.")

        # --- EXPORTAÇÃO COMPLETA COM FILTRO ---
        st.write("---")
        st.subheader("💾 Exportação de Bases e Auditoria")
        cexp1, cexp2, cexp3 = st.columns(3)
        if not df_qualitor.empty: cexp1.download_button("📥 BAIXAR BASE QUALITOR (CSV)", df_qualitor.to_csv(index=False).encode('utf-8-sig'), file_name=f"Qualitor_{data_hoje().replace('/','-')}.csv", mime="text/csv", use_container_width=True)
        if not df_azix_data.empty: cexp2.download_button("📥 BAIXAR BASE AZIX/MKTP (CSV)", df_azix_data.to_csv(index=False).encode('utf-8-sig'), file_name=f"Azix_{data_hoje().replace('/','-')}.csv", mime="text/csv", use_container_width=True)
        
        if not df_logs_periodo.empty: 
            df_logs_export = df_logs_periodo.drop(columns=['DataReal'])
            nome_arquivo_logs = f"Logs_{data_inicio.strftime('%d%m')}_a_{data_fim.strftime('%d%m')}.csv"
            cexp3.download_button("📥 BAIXAR LOGS FILTRADOS (CSV)", df_logs_export.to_csv(index=False).encode('utf-8-sig'), file_name=nome_arquivo_logs, mime="text/csv", use_container_width=True)
        else: cexp3.info("Sem logs para este período.")

        # --- GERADOR DE RELATÓRIO EXECUTIVO (PDF) ---
        st.write("---")
        with st.expander("📄 Gerar Relatório Executivo Oficial (PDF)"):
            aviso_pdf = st.text_input("Observação (Opcional):", placeholder="Ex: Operação Azix com volume alto...")
            if st.button("🖨️ Mapear Dados e Criar PDF", use_container_width=True):
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
            
        # --- ROBÔ IMPORTADOR ---
        st.write("---")
        st.subheader("📥 Robô Importador Universal")
        tipo_importacao = st.radio("Escolha a Base:", ["1. Qualitor (Substituição)", "2. Azix (Injeção Inteligente)"])
        
        with st.expander("Abrir Ferramenta"):
            arquivo_excel = st.file_uploader("Arraste o arquivo aqui (.xlsx ou .csv)", type=["xlsx", "xls", "csv"])
            if arquivo_excel is not None:
                try:
                    with st.spinner("Lendo..."):
                        if arquivo_excel.name.endswith('.csv'): df_bruto = pd.read_csv(arquivo_excel, sep=None, engine='python', encoding='latin-1')
                        else: df_bruto = pd.read_excel(arquivo_excel)
                    
                    if tipo_importacao == "1. Qualitor (Substituição)":
                        if 'PROCESSO' in df_bruto.columns and 'Chamado' in df_bruto.columns:
                            # REGRA DE OURO QUALITOR
                            df_filtrado = df_bruto[~df_bruto['PROCESSO'].astype(str).str.contains("SOLICITANTE ATUALIZAR INFORMAÇÕES", na=False)].copy()
                            de_para = {
                                "(SAC) - ARREPENDIMENTO V3": "Arrependimento", "(SAC) - CANCELAMENTO V3": "Cancelamento de pedido",
                                "(SAC) - ATRASO V3": "Atraso de Entrega", "(SAC) - PRODUTO ERRADO V3": "Produto Errado",
                                "(SAC) - AVARIA V3": "Avaria", "(SAC) - ESTORNADOS": "Estornados", "(SAC) - EXTRAVIO V6": "Extravio"
                            }
                            df_filtrado['Etapa_Limpa'] = df_filtrado['PROCESSO'].map(de_para).fillna(df_filtrado['PROCESSO'])
                            
                            # Identifica quem é MKTP
                            is_mktp = pd.Series(False, index=df_filtrado.index)
                            if 'Etapa' in df_filtrado.columns:
                                is_mktp = df_filtrado['Etapa'].astype(str).str.contains("REEMBOLSO MKTP", na=False, case=False)
                                df_filtrado.loc[is_mktp, 'Etapa_Limpa'] = "Reembolso MKTP"
                            
                            def def_prioridade(linha): return "🔥 Prioridade (Vence Hoje)" if "PRIORIDADE 1" in str(linha).upper() else "Normal ✅"
                            df_filtrado['SLA_Final'] = df_filtrado['Lista'].apply(def_prioridade) if 'Lista' in df_filtrado.columns else "Normal ✅"
                            
                            # ---> 🚀 NOVA REGRA: MUDAR ETAPA PARA "Prioridade" (Exceto MKTP) <---
                            if 'Lista' in df_filtrado.columns:
                                is_prio = df_filtrado['Lista'].astype(str).str.upper().str.contains("PRIORIDADE 1", na=False)
                                df_filtrado.loc[is_prio & ~is_mktp, 'Etapa_Limpa'] = "Prioridade"
                            
                            df_novo = pd.DataFrame()
                            df_novo['ID'] = ""; df_novo['Dados'] = df_filtrado['Chamado'].astype(str).str.replace(r'\.0$', '', regex=True); df_novo['Status'] = "Pendente"; df_novo['Etapa'] = df_filtrado['Etapa_Limpa']; df_novo['SLA'] = df_filtrado['SLA_Final']; df_novo['Responsavel'] = ""; df_novo['Inicio'] = ""; df_novo['Data_Conclusao'] = "" 
                            df_pronto = df_novo.drop_duplicates(subset=['Dados'])
                            
                            def def_prioridade(linha): return "🔥 Prioridade (Vence Hoje)" if "PRIORIDADE 1" in str(linha).upper() else "Normal ✅"
                            df_filtrado['SLA_Final'] = df_filtrado['Lista'].apply(def_prioridade) if 'Lista' in df_filtrado.columns else "Normal ✅"
                            
                            df_novo = pd.DataFrame()
                            df_novo['ID'] = ""; df_novo['Dados'] = df_filtrado['Chamado'].astype(str).str.replace(r'\.0$', '', regex=True); df_novo['Status'] = "Pendente"; df_novo['Etapa'] = df_filtrado['Etapa_Limpa']; df_novo['SLA'] = df_filtrado['SLA_Final']; df_novo['Responsavel'] = ""; df_novo['Inicio'] = ""; df_novo['Data_Conclusao'] = "" 
                            df_pronto = df_novo.drop_duplicates(subset=['Dados'])
                            
                            qtd_novos = len(df_pronto)
                            qtd_prioridade = len(df_pronto[df_pronto['SLA'].str.contains("Prioridade")])
                            
                            st.success("✅ Análise Qualitor concluída!")
                            cc1, cc2 = st.columns(2)
                            cc1.markdown(f"<div style='background-color: #f8fafc; padding: 15px; border-radius: 8px; border: 1px solid #e2e8f0;'><h3 style='margin:0; color:#0f172a;'>📦 {qtd_novos}</h3><p style='margin:0; color:#64748b;'>Chamados Identificados</p></div>", unsafe_allow_html=True)
                            
                            if 'Etapa' in df_pronto.columns:
                                top_etapas = df_pronto['Etapa'].value_counts().head(3)
                                txt_etapas = "<br>".join([f"<b>{k}:</b> {v}" for k, v in top_etapas.items()])
                                cc2.markdown(f"<div style='background-color: #f8fafc; padding: 15px; border-radius: 8px; border: 1px solid #e2e8f0;'><p style='margin:0; color:#64748b; font-size:0.9em;'>Top Assuntos:</p><div style='color:#0f172a;'>{txt_etapas}</div></div>", unsafe_allow_html=True)
                            
                            st.write("")
                            if qtd_prioridade > 0:
                                st.error(f"🔥 ALERTA: Temos **{qtd_prioridade}** chamados com PRIORIDADE MÁXIMA (Vence Hoje) neste lote. Foco total!")

                            if st.button("🚀 SUBSTITUIR BASE QUALITOR", type="primary"):
                                df_limpo = df_pronto.fillna("").replace(['nan', 'NaN', 'NaT', 'None'], "")
                                aba_chamados.clear(); aba_chamados.append_rows([df_limpo.columns.tolist()] + df_limpo.values.tolist())
                                registrar_log(usuario, "Importou Base Qualitor")
                                st.success("Atualizado!"); st.cache_data.clear(); time.sleep(1); st.rerun()
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
                            st.success("Fila Azix atualizada!"); st.cache_data.clear(); time.sleep(1); st.rerun()
                except Exception as e: st.error(f"Erro no robô: {e}")

        st.write("---")
        st.subheader("🧹 Limpeza e Ações de Emergência")
        if st.button("🧹 APAGAR TODOS OS CONCLUÍDOS (AZIX)"):
            if not df_azix_data.empty:
                df_ativos = df_azix_data[df_azix_data['Status'] != 'Concluido'].copy()
                aba_azix.clear()
                aba_azix.append_rows([df_ativos.columns.tolist()] + df_ativos.values.tolist())
                st.success("Faxina feita!"); st.cache_data.clear(); time.sleep(1); st.rerun()
                
        em_andamento = df_qualitor[df_qualitor['Status'] == 'Em Andamento'].copy() if not df_qualitor.empty else pd.DataFrame()
        if not em_andamento.empty:
            opcoes = em_andamento.apply(lambda x: f"L{x.name + 2} - ID {x['ID']} - {x['Dados']} ({x['Responsavel']})", axis=1).tolist()
            selecionado = st.selectbox("Selecione um chamado travado (Qualitor):", [""] + opcoes)
            if selecionado:
                linha_trava = int(selecionado.split(" - ")[0].replace("L", ""))
                col_dev, col_forcar = st.columns(2)
                if col_dev.button("↩️ Devolver à Fila"):
                    aba_chamados.update_cell(linha_trava, COL_STATUS, "Pendente"); aba_chamados.update_cell(linha_trava, COL_RESP, ""); aba_chamados.update_cell(linha_trava, COL_INICIO, "") 
                    registrar_log(usuario, f"ADMIN: Devolveu linha {linha_trava}"); st.success("Devolvido!"); st.cache_data.clear(); time.sleep(1); st.rerun()
                if col_forcar.button("🏁 Forçar Conclusão"):
                    aba_chamados.update_cell(linha_trava, COL_STATUS, "Concluido"); aba_chamados.update_cell(linha_trava, COL_FIM, hora_texto()) 
                    registrar_log(usuario, f"ADMIN: Forçou conclusão linha {linha_trava}"); st.success("Encerrado!"); st.cache_data.clear(); time.sleep(1); st.rerun()

    # =========================================================================
    # 🧠 SQUAD 1: VISÃO DA CHARLENE (TRATATIVAS AZIX COM SLA E BUSCA ATIVA)
    # =========================================================================
    elif usuario in SQUAD_AZIX:
        st.title("🛡️ Painel Azix - Tratativas")
        if status_real != "Disponivel": st.warning(f"⚠️ **VOCÊ ESTÁ EM PAUSA ({status_real})**")
        else:
            if df.empty or 'Status' not in df.columns:
                st.info("📭 A base de dados Azix está vazia. O Gestor precisa importar os dados no Painel de Gestão.")
                if st.button("🔄 Recarregar Fila"): st.cache_data.clear(); st.rerun()
            else:
                meu_chamado = df[(df['Status'] == 'Em Andamento') & (df['Responsavel'] == usuario)]
                if len(meu_chamado) > 0:
                    dados = meu_chamado.iloc[0]
                    idx_linha = int(meu_chamado.index[0]) + 2 
                    
                    sla_badge = dados.get('Status_SLA', 'SLA não calculado')
                    if 'Atrasado' in sla_badge or 'Vence Hoje' in sla_badge: st.error(f"SLA: {sla_badge}")
                    else: st.info(f"SLA: {sla_badge}")
                    
                    st.markdown("### 📋 Ficha Detalhada do Pedido")
                    with st.container():
                        st.markdown('<div style="background-color: #f8fafc; padding: 20px; border-radius: 10px; border-left: 5px solid #38bdf8;">', unsafe_allow_html=True)
                        for col in df.columns:
                            if col not in ['ID', 'Status', 'Responsavel', 'Inicio', 'Data_Conclusao', 'SLA', 'Peso_SLA', 'Assentamentos', 'Data_Entrada', 'Status_SLA', 'Validacao_Receita']:
                                val = dados.get(col, '')
                                if pd.notna(val) and str(val).strip() != '': st.markdown(f"**{col}:** {val}")
                        st.markdown('</div><br>', unsafe_allow_html=True)

                    st.markdown("### 📝 Histórico (CRM)")
                    historico_atual = str(dados.get('Assentamentos', ''))
                    if historico_atual.strip() != '' and historico_atual != 'nan':
                        historico_formatado = re.sub(r'(\[\d{2}/\d{2}/\d{4})', r'\n\n\1', historico_atual).strip()
                        st.info(historico_formatado)
                    
                    # --- NOVA VALIDAÇÃO DA RECEITA ---
                    st.markdown("#### 🏛️ Validação Endereço Receita")
                    st.caption("Preencha para gerar as métricas de divergência de endereço.")
                    val_atual = str(dados.get('Validacao_Receita', 'Não Tratado'))
                    opcoes_val = ["Não Tratado", "Cliente Aceitou", "Cliente Negou", "Aguardando Cliente"]
                    idx_val = opcoes_val.index(val_atual) if val_atual in opcoes_val else 0
                    escolha_validacao = st.radio("Selecione o status desta tratativa:", opcoes_val, index=idx_val, horizontal=True)

                    novo_assentamento = st.text_area("Nova observação:", placeholder="Ex: Cliente contactado...")

                    st.write("---")
                    if 'confirmar_azix' not in st.session_state: st.session_state['confirmar_azix'] = False
                    
                    if not st.session_state['confirmar_azix']:
                        c_fim, c_pausa = st.columns(2)
                        if c_fim.button("✅ FINALIZAR TRATATIVA", type="primary", use_container_width=True): st.session_state['confirmar_azix'] = True; st.rerun()
                        if c_pausa.button("⏳ DEVOLVER À FILA", use_container_width=True):
                            aba_atual.update_cell(idx_linha, COL_STATUS, "Pendente - Retorno")
                            aba_atual.update_cell(idx_linha, COL_RESP, "")
                            aba_atual.update_cell(idx_linha, COL_INICIO, "") 
                            
                            if 'Validacao_Receita' in df.columns:
                                col_val_idx = df.columns.tolist().index('Validacao_Receita') + 1
                                aba_atual.update_cell(idx_linha, col_val_idx, escolha_validacao)
                            
                            if novo_assentamento:
                                col_ass = df.columns.tolist().index('Assentamentos') + 1
                                aba_atual.update_cell(idx_linha, col_ass, f"{historico_atual}\n[{hora_texto()}] {usuario}: {novo_assentamento}".strip())
                            
                            num_pedido = str(dados.get('Nº Pedido venda', dados.get('Dados', '')))
                            if 'ignorados_azix' not in st.session_state: st.session_state['ignorados_azix'] = []
                            st.session_state['ignorados_azix'].append(num_pedido)
                            
                            registrar_log(usuario, f"Devolveu Azix à Fila ({num_pedido})"); st.cache_data.clear(); time.sleep(1.5); st.rerun()
                    else:
                        st.warning("Confirma a conclusão?")
                        cy, cn = st.columns(2)
                        if cy.button("👍 SIM, FINALIZAR"):
                            if 'Validacao_Receita' in df.columns:
                                col_val_idx = df.columns.tolist().index('Validacao_Receita') + 1
                                aba_atual.update_cell(idx_linha, col_val_idx, escolha_validacao)

                            if novo_assentamento:
                                col_ass = df.columns.tolist().index('Assentamentos') + 1
                                aba_atual.update_cell(idx_linha, col_ass, f"{historico_atual}\n[{hora_texto()}] {usuario}: {novo_assentamento}".strip())
                            
                            num_pedido = dados.get('Nº Pedido venda', dados.get('Dados', ''))
                            if is_marketplace(num_pedido):
                                aba_atual.update_cell(idx_linha, COL_STATUS, "Aguardando Reivindicação"); aba_atual.update_cell(idx_linha, COL_RESP, "") 
                                registrar_log(usuario, f"Azix para Mktp ({num_pedido})"); st.success("Encaminhado para Reivindicações!")
                            else:
                                aba_atual.update_cell(idx_linha, COL_STATUS, "Concluido"); aba_atual.update_cell(idx_linha, COL_FIM, hora_texto()) 
                                registrar_log(usuario, f"Concluiu Azix ({num_pedido})")
                                verificar_meta_baloes(usuario, ranking_global, META_DIARIA)
                            
                            st.session_state['confirmar_azix'] = False; st.cache_data.clear(); time.sleep(1.5); st.rerun()
                        if cn.button("❌ NÃO"): st.session_state['confirmar_azix'] = False; st.rerun()

                else:
                    # --- FILAS SEPARADAS (NOVOS E RETORNOS) ---
                    fila_novos = df[(df['Status'] == 'Pendente') & (df['Responsavel'] == "")].copy()
                    fila_retornos = df[(df['Status'] == 'Pendente - Retorno') & (df['Responsavel'] == "")].copy()
                    
                    st.markdown("### 🔍 Busca Ativa (Puxar Pedido Específico)")
                    c_busca, c_btn = st.columns([3, 1])
                    pedido_busca = c_busca.text_input("Nº do Pedido:", placeholder="Ex: MAGALU_123", label_visibility="collapsed")
                    if c_btn.button("🔍 Buscar e Puxar", use_container_width=True):
                        if pedido_busca.strip():
                            if 'Nº Pedido venda' in df.columns:
                                fila_geral = df[(df['Status'].isin(['Pendente', 'Pendente - Retorno'])) & (df['Responsavel'] == "")]
                                alvo = fila_geral[fila_geral['Nº Pedido venda'].astype(str).str.contains(pedido_busca.strip(), case=False, na=False)]
                                if not alvo.empty:
                                    item = alvo.iloc[0]; idx_linha = int(item.name) + 2
                                    aba_atual.update_cell(idx_linha, COL_STATUS, "Em Andamento"); aba_atual.update_cell(idx_linha, COL_RESP, usuario); aba_atual.update_cell(idx_linha, COL_INICIO, hora_texto())
                                    registrar_log(usuario, f"Busca Ativa: Pegou {pedido_busca}")
                                    st.success("Encontrado! Puxando para sua tela..."); st.cache_data.clear(); time.sleep(1); st.rerun()
                                else: st.error("Pedido não encontrado na fila livre, ou já está com outro operador.")
                            else: st.error("Coluna 'Nº Pedido venda' não encontrada na base.")
                        else: st.warning("Digite um número de pedido.")
                    st.write("---")
                    
                    # --- TABS PARA AS FILAS ---
                    tab1, tab2 = st.tabs(["🆕 Fila de Novos", "⏳ Pendentes de Retorno"])
                    
                    with tab1:
                        st.metric("📦 Fila Novos", len(fila_novos))
                        if len(fila_novos) > 0:
                            if st.button("📥 PUXAR NOVO PEDIDO", type="primary", use_container_width=True, key="btn_novo"):
                                item = fila_novos.iloc[0]
                                idx_linha = int(item.name) + 2 
                                num_pedido = str(item.get('Nº Pedido venda', ''))
                                aba_atual.update_cell(idx_linha, COL_STATUS, "Em Andamento")
                                aba_atual.update_cell(idx_linha, COL_RESP, usuario)
                                aba_atual.update_cell(idx_linha, COL_INICIO, hora_texto())          
                                registrar_log(usuario, f"Pegou Pedido Azix ({num_pedido})")
                                st.cache_data.clear(); time.sleep(1); st.rerun()
                        else: st_autorefresh(interval=60000, key="refresh_azix_novos")
                    
                    with tab2:
                        st.metric("📦 Fila Retornos", len(fila_retornos))
                        if len(fila_retornos) > 0:
                            if st.button("📥 PUXAR RETORNO", type="primary", use_container_width=True, key="btn_retorno"):
                                if 'ignorados_azix' not in st.session_state: st.session_state['ignorados_azix'] = []
                                fila_limpa = fila_retornos[~fila_retornos['Nº Pedido venda'].astype(str).isin(st.session_state['ignorados_azix'])]
                                
                                if not fila_limpa.empty: item = fila_limpa.iloc[0]
                                else: st.session_state['ignorados_azix'] = []; item = fila_retornos.iloc[0]

                                idx_linha = int(item.name) + 2 
                                num_pedido = str(item.get('Nº Pedido venda', ''))
                                aba_atual.update_cell(idx_linha, COL_STATUS, "Em Andamento")
                                aba_atual.update_cell(idx_linha, COL_RESP, usuario)
                                aba_atual.update_cell(idx_linha, COL_INICIO, hora_texto())          
                                registrar_log(usuario, f"Pegou Retorno Azix ({num_pedido})")
                                st.cache_data.clear(); time.sleep(1); st.rerun()
                        else: st_autorefresh(interval=60000, key="refresh_azix")

    # =========================================================================
    # 🛒 SQUAD 2: VISÃO DOS OPERADORES DE REIVINDICAÇÕES MARKETPLACE
    # =========================================================================
    elif usuario in SQUAD_MKTP:
        st.title("🛒 Painel de Reivindicações (Mktp)")
        if status_real != "Disponivel": st.warning(f"⚠️ **VOCÊ ESTÁ EM PAUSA ({status_real})**")
        else:
            if df.empty or 'Status' not in df.columns:
                st.info("📭 A base de dados Azix está vazia. O Gestor precisa importar os dados no Painel de Gestão.")
                if st.button("🔄 Recarregar Fila"): st.cache_data.clear(); st.rerun()
            else:
                meu_chamado = df[(df['Status'] == 'Em Tratativa Mktp') & (df['Responsavel'] == usuario)]
                if len(meu_chamado) > 0:
                    dados = meu_chamado.iloc[0]
                    idx_linha = int(meu_chamado.index[0]) + 2 
                    num_pedido = dados.get('Nº Pedido venda', 'N/A')
                    
                    st.success("🟢 TRATANDO REIVINDICAÇÃO...")
                    st.markdown(f"### 📦 Pedido: **{num_pedido}**")
                    
                    with st.container():
                        st.markdown('<div style="background-color: #fffbeb; padding: 20px; border-radius: 10px; border-left: 5px solid #f59e0b;">', unsafe_allow_html=True)
                        for col in df.columns:
                            if col not in ['ID', 'Status', 'Responsavel', 'Inicio', 'Data_Conclusao', 'SLA', 'Peso_SLA', 'Assentamentos', 'Data_Entrada', 'Status_SLA']:
                                val = dados.get(col, '')
                                if pd.notna(val) and str(val).strip() != '': st.markdown(f"**{col}:** {val}")
                        st.markdown('</div><br>', unsafe_allow_html=True)

                    st.markdown("### 📝 Histórico (CRM)")
                    historico_atual = str(dados.get('Assentamentos', ''))
                    if historico_atual.strip() != '' and historico_atual != 'nan':
                        historico_formatado = re.sub(r'(\[\d{2}/\d{2}/\d{4})', r'\n\n\1', historico_atual).strip()
                        st.info(historico_formatado)
                    novo_assentamento = st.text_area("Observação final:")

                    st.write("---")
                    if 'confirmar_mktp' not in st.session_state: st.session_state['confirmar_mktp'] = False
                    
                    if not st.session_state['confirmar_mktp']:
                        if st.button("✅ ENCERRAR REIVINDICAÇÃO", type="primary", use_container_width=True): st.session_state['confirmar_mktp'] = True; st.rerun()
                    else:
                        st.warning("Confirma o encerramento?")
                        cy, cn = st.columns(2)
                        if cy.button("👍 SIM"):
                            if novo_assentamento:
                                col_ass = df.columns.tolist().index('Assentamentos') + 1
                                aba_atual.update_cell(idx_linha, col_ass, f"{historico_atual}\n[{hora_texto()}] {usuario}: {novo_assentamento}".strip())
                            aba_atual.update_cell(idx_linha, COL_STATUS, "Concluido"); aba_atual.update_cell(idx_linha, COL_FIM, hora_texto()) 
                            registrar_log(usuario, f"Reivindicação Encerrada")
                            verificar_meta_baloes(usuario, ranking_global, META_DIARIA)
                            st.session_state['confirmar_mktp'] = False
                            st.cache_data.clear(); time.sleep(1); st.rerun()
                        if cn.button("❌ NÃO"): st.session_state['confirmar_mktp'] = False; st.rerun()
                else:
                    fila = df[(df['Status'] == 'Aguardando Reivindicação')].copy()
                    if not fila.empty and "Todas" not in minhas_etapas and "todas" not in [e.lower() for e in minhas_etapas]:
                        col_busca = 'Nº Pedido venda' if 'Nº Pedido venda' in fila.columns else 'Dados'
                        filtro = pd.Series(False, index=fila.index)
                        for palavra in minhas_etapas:
                            if palavra.strip(): filtro = filtro | fila[col_busca].astype(str).str.contains(palavra.strip(), case=False, na=False)
                        fila = fila[filtro]
                    
                    st.markdown("### 🔍 Busca Ativa (Puxar Pedido Específico)")
                    c_busca, c_btn = st.columns([3, 1])
                    pedido_busca = c_busca.text_input("Nº do Pedido:", placeholder="Ex: MAGALU_123", label_visibility="collapsed")
                    if c_btn.button("🔍 Buscar e Puxar", use_container_width=True):
                        if pedido_busca.strip():
                            if 'Nº Pedido venda' in fila.columns:
                                alvo = fila[fila['Nº Pedido venda'].astype(str).str.contains(pedido_busca.strip(), case=False, na=False)]
                                if not alvo.empty:
                                    item = alvo.iloc[0]; idx_linha = int(item.name) + 2
                                    aba_atual.update_cell(idx_linha, COL_STATUS, "Em Tratativa Mktp"); aba_atual.update_cell(idx_linha, COL_RESP, usuario); aba_atual.update_cell(idx_linha, COL_INICIO, hora_texto())
                                    registrar_log(usuario, f"Busca Ativa Mktp: Pegou {pedido_busca}")
                                    st.success("Encontrado! Puxando para sua tela..."); st.cache_data.clear(); time.sleep(1); st.rerun()
                                else: st.error("Pedido não encontrado na sua fila de reivindicações.")
                            else: st.error("Coluna 'Nº Pedido venda' não encontrada na base.")
                        else: st.warning("Digite um número de pedido.")
                    st.write("---")

                    st.metric("🚨 Reivindicações", len(fila))
                    if st.button("🔄 Atualizar Fila"): st.cache_data.clear(); st.rerun()
                    if len(fila) > 0:
                        if st.button("📥 REIVINDICAR PRÓXIMO", type="primary", use_container_width=True):
                            item = fila.iloc[0]
                            idx_linha = int(item.name) + 2 
                            num_pedido = str(item.get('Nº Pedido venda', '')) # Puxa o número do pedido
                            
                            aba_atual.update_cell(idx_linha, COL_STATUS, "Em Tratativa Mktp")
                            aba_atual.update_cell(idx_linha, COL_RESP, usuario)
                            aba_atual.update_cell(idx_linha, COL_INICIO, hora_texto())          
                            registrar_log(usuario, f"Pegou Mktp ({num_pedido})") # Grava com o número!
                            st.cache_data.clear(); time.sleep(1); st.rerun()
                    else: st_autorefresh(interval=60000, key="refresh_mktp")

    # =========================================================================
    # 👷 SQUAD 3: VISÃO PADRÃO (OPERADOR QUALITOR)
    # =========================================================================
    else:
        if status_real != "Disponivel": st.warning(f"⚠️ **VOCÊ ESTÁ EM PAUSA ({status_real})**")
        else:
            st.success(f"🟢 ONLINE - {sub_saudacao}")
            
            if df.empty or 'Status' not in df.columns:
                st.info("📭 A base de dados Qualitor está vazia. O Gestor precisa importar os dados.")
                if st.button("🔄 Recarregar Fila"): st.cache_data.clear(); st.rerun()
            else:
                meu_chamado = df[(df['Status'] == 'Em Andamento') & (df['Responsavel'] == usuario)]
                
                if len(meu_chamado) > 0:
                    dados = meu_chamado.iloc[0]
                    num = dados.get('Dados', 'N/A') 
                    sla_atual = str(dados.get('SLA', 'Sem Info'))
                    
                    if 'fora' in sla_atual.lower(): st.error(f"🔥 ALERTA DE SLA: {sla_atual}")
                    else: st.info(f"✅ Status do SLA: {sla_atual}")

                    st.markdown(f"### 📞 Chamado: **{num}** | Etapa: **{dados.get('Etapa', 'N/A')}**")
                    if str(num) != 'N/A': 
                        link_q = f"https://frigelar.qualitorsoftware.com/html/hd/hdchamado/cadastro_chamado.php?cdchamado={num}"
                        
                        st.error("🛡️ **Bloqueio de TI (Qualitor + Microsoft SSO)**")
                        st.markdown("<span style='font-size: 0.9em;'>Impossibilitado temporariamente via link direto</span>", unsafe_allow_html=True)
                        st.markdown("1️⃣ Clique no **ícone de copiar** no canto superior direito da caixa abaixo.<br>2️⃣ Pressione **Ctrl + T** (Nova Aba) e **Ctrl + V** (Colar).", unsafe_allow_html=True)
                        st.code(link_q, language="text")
                    
                    with st.expander("✨ Resumir Histórico do Chamado (Inteligência Artificial)"):
                        historico = st.text_area("Cole aqui os assentamentos do cliente para análise rápida:", height=100)
                        if st.button("Mastigar Histórico"):
                            if ia_ativa and historico:
                                with st.spinner("Processando os dados..."):
                                    try:
                                        resp = modelo_oraculo.generate_content(f"Analise este histórico de atendimento e devolva os 3 pontos mais importantes (causa, situação atual e o que o cliente quer). Seja extremamente resumido:\n\n{historico}")
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
                            aba_chamados.update_cell(int(meu_chamado.index[0])+2, COL_STATUS, "Concluido")
                            aba_chamados.update_cell(int(meu_chamado.index[0])+2, COL_FIM, hora_texto()) 
                            registrar_log(usuario, f"Finalizou {num}")
                            verificar_meta_baloes(usuario, ranking_global, META_DIARIA)
                            st.session_state['confirmar'] = False
                            st.cache_data.clear(); time.sleep(1); st.rerun()
                        if cn.button("❌ NÃO"): st.session_state['confirmar'] = False; st.rerun()
                else:
                    fila = df[(df['Status'] == 'Pendente') & (df['Responsavel'] == "")].copy()
                    if "Todas" not in minhas_etapas and "todas" not in [e.lower() for e in minhas_etapas]:
                        fila = fila[fila['Etapa'].astype(str).isin(minhas_etapas)]
                    
                    st.markdown("### 🔍 Busca Ativa (Puxar Chamado Específico)")
                    c_busca, c_btn = st.columns([3, 1])
                    pedido_busca = c_busca.text_input("Nº do Chamado:", placeholder="Ex: 384405", label_visibility="collapsed")
                    if c_btn.button("🔍 Buscar e Puxar", use_container_width=True):
                        if pedido_busca.strip():
                            alvo = fila[fila['Dados'].astype(str).str.contains(pedido_busca.strip(), case=False, na=False)]
                            if not alvo.empty:
                                item = alvo.iloc[0]; idx_linha = int(item.name) + 2
                                aba_atual.update_cell(idx_linha, COL_STATUS, "Em Andamento"); aba_atual.update_cell(idx_linha, COL_RESP, usuario); aba_atual.update_cell(idx_linha, COL_INICIO, hora_texto())
                                registrar_log(usuario, f"Busca Ativa Qualitor: Pegou {pedido_busca}")
                                st.success("Encontrado! Puxando para sua tela..."); st.cache_data.clear(); time.sleep(1); st.rerun()
                            else: st.error("Chamado não encontrado na fila permitida ou já em atendimento.")
                        else: st.warning("Digite um número de chamado.")
                    st.write("---")

                    qtd = len(fila)
                    c_f, c_r = st.columns([3,1])
                    c_f.metric("Sua Fila Qualitor", qtd)
                    if c_r.button("🔄 Atualizar Fila"): st.cache_data.clear(); st.rerun()
                    
                    if qtd > 0:
                        # ✅ O ÚNICO E VERDADEIRO BOTÃO!
                        if st.button("📥 PEGAR PRÓXIMO", type="primary", use_container_width=True):
                            if 'SLA' in fila.columns:
                                fila['Peso_SLA'] = fila['SLA'].astype(str).apply(lambda x: 1 if 'fora' in x.lower() else 2)
                                fila = fila.sort_values(by='Peso_SLA', kind='stable')
                                
                            item = fila.iloc[0]
                            idx_linha = int(item.name) + 2 
                            num_chamado = str(item.get('Dados', '')).replace('.0', '') # Puxa o número do chamado
                            
                            aba_chamados.update_cell(idx_linha, COL_STATUS, "Em Andamento")
                            aba_chamados.update_cell(idx_linha, COL_RESP, usuario)
                            aba_chamados.update_cell(idx_linha, COL_INICIO, hora_texto())          
                            registrar_log(usuario, f"Pegou chamado Qualitor ({num_chamado})") # Grava com o número!
                            st.cache_data.clear(); time.sleep(1); st.rerun()
                    else: 
                        st_autorefresh(interval=60000, key="refresh_fila_vazia")

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
                        
                        st.dataframe(hist_hoje[cols_show].tail(15), hide_index=True, use_container_width=True)
                else:
                    st.caption("Nenhum chamado concluído por você hoje, ainda. Vamos lá!")

    # ===================================================
    # 🧙‍♂️ GAVETA DO ORÁCULO E TRANSPORTADORAS (PARA TODOS)
    # ===================================================
    if usuario != "TV" and not modo_gerente:
        st.write("---")
        with st.expander("🧙‍♂️ Oráculo Frigelar"):
            pergunta = st.text_input("O que você precisa saber?")
            if st.button("✨ Perguntar"):
                if ia_ativa:
                    try:
                        with open("regras_operacao.txt", "r", encoding="utf-8") as f:
                            resposta = modelo_oraculo.generate_content(f"Seja o Oráculo do SAC Frigelar. Responda baseado no manual:\n{f.read()}\n\nPergunta: {pergunta}")
                            st.info(resposta.text)
                    except Exception as e: st.error(f"Erro: {e}")
                else: st.error(f"IA não configurada.")
                
        st.write("---")
        with st.expander("🚚 Agenda de Contatos - Transportadoras"):
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

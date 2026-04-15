import streamlit as st
import pandas as pd
import gspread
from datetime import datetime, timedelta
import time
import pytz
import os
import google.generativeai as genai
from streamlit_autorefresh import st_autorefresh
from fpdf import FPDF
import unicodedata

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(page_title="Esteira Qualitor", page_icon="🎫", layout="wide")

# --- 👑 ADMINISTRAÇÃO ---
ADMINS = ["Eduardo", "EduardoSouza", "Gestor", "Lopes"] 

# --- 🧠 CONFIGURAÇÃO DA IA (ORÁCULO) ---
try:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    
    # MODO AUTO-DESCOBERTA DE MODELO
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

# --- CONEXÃO BLINDADA (ANTI-TRAVAMENTO DO GOOGLE) ---
@st.cache_resource
def conectar_e_abrir_abas():
    try:
        # 1. Verifica Credenciais
        if os.path.exists("credentials.json"):
            client = gspread.service_account(filename="credentials.json")
        else:
            try:
                creds_dict = st.secrets["gcp_service_account"]
                client = gspread.service_account_from_dict(creds_dict)
            except:
                return "🚨 ERRO: Credenciais não encontradas. Faltam os Secrets ou o credentials.json.", None, None, None, None
        
        # 2. Loop de Paciência (Tenta até 10 vezes se o Google travar)
        erro_real = ""
        for tentativa in range(10):
            try:
                sh = client.open("Chamados_Qualitor") # NOME OFICIAL DE PRODUÇÃO
                abas = sh.worksheets()
                
                if len(abas) >= 2:
                    aba_chamados = abas[0] if len(abas) > 0 else None
                    aba_users = abas[1] if len(abas) > 1 else None
                    aba_logs = abas[2] if len(abas) > 2 else None
                    aba_transp = abas[3] if len(abas) > 3 else None
                    return sh, aba_chamados, aba_users, aba_logs, aba_transp
                else:
                    erro_real = "A planilha tem menos de 2 abas visíveis."
            except Exception as e:
                erro_real = str(e)
                time.sleep(2 + tentativa) # Espera progressiva antes de tentar de novo
                
        return f"Falha após 10 tentativas. Último erro: {erro_real}", None, None, None, None
    except Exception as e:
        return f"Erro Crítico: {str(e)}", None, None, None, None

# --- FUNÇÕES DE TEMPO E LÓGICA ---
def hora_brasil():
    fuso = pytz.timezone('America/Sao_Paulo')
    return datetime.now(fuso) 

def hora_texto():
    return hora_brasil().strftime("%d/%m/%Y %H:%M:%S")

def data_hoje():
    return hora_brasil().strftime("%d/%m/%Y")

def calcular_minutos(data_inicio_str):
    try:
        inicio = datetime.strptime(data_inicio_str, "%d/%m/%Y %H:%M:%S")
        inicio = inicio.replace(tzinfo=pytz.timezone('America/Sao_Paulo'))
        agora = hora_brasil()
        diferenca = agora - inicio
        return int(diferenca.total_seconds() / 60)
    except: return 0

def formatar_tempo(minutos):
    horas, resto = divmod(minutos, 60)
    return f"{horas}h {resto}min"

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

def minutos_gastos(inicio_str, fim_str):
    try:
        fmt = "%d/%m/%Y %H:%M:%S"
        i = datetime.strptime(str(inicio_str), fmt)
        f = datetime.strptime(str(fim_str), fmt)
        return (f - i).total_seconds() / 60
    except: return 0

# --- INICIAR CONEXÃO (COM DIAGNÓSTICO DETETIVE) ---
sh, aba_chamados, aba_users, aba_logs, aba_transp = conectar_e_abrir_abas()

if isinstance(sh, str):
    st.error("❌ A conexão falhou antes de carregar as abas.")
    st.warning(f"Diagnóstico: {sh}")
    if "SpreadsheetNotFound" in sh:
        st.info("Logou no Google, mas não achou a planilha. Verifique se o nome está exatamente 'Chamados_Qualitor' e se o e-mail do robô está como Editor.")
    if st.button("Tentar conectar novamente agora"): st.rerun()
    st.stop()
elif aba_chamados is None or aba_users is None:
    st.error("Erro desconhecido ao tentar carregar as abas principais.")
    st.stop()

# --- FUNÇÕES DE DADOS E LOGS (COM CACHE) ---
def registrar_log(usuario, acao):
    try: aba_logs.append_row([usuario, acao, hora_texto()])
    except: pass

@st.cache_data(ttl=15)
def carregar_dados_chamados():
    try:
        df = pd.DataFrame(aba_chamados.get_all_records())
        if 'Dados' in df.columns:
            df['Dados'] = df['Dados'].astype(str).str.replace(r'\.0$', '', regex=True)
        return df
    except: return pd.DataFrame()

@st.cache_data(ttl=30)
def carregar_status_equipe():
    try:
        dados = aba_users.get_all_values()
        if not dados: return pd.DataFrame()
        df = pd.DataFrame(dados[1:], columns=dados[0])
        df = df.loc[:, df.columns != ''] 
        return df
    except Exception as e:
        return pd.DataFrame()

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
        if dados[0][0].lower() in ['usuario', 'nome', 'operador']:
            df_l = pd.DataFrame(dados[1:], columns=["Usuario", "Acao", "DataHora"])
        else:
            df_l = pd.DataFrame(dados, columns=["Usuario", "Acao", "DataHora"])
        return df_l
    except: return pd.DataFrame()

# --- RANKING GLOBAL (MEMÓRIA PERPÉTUA PARA O PDF) ---
df_logs_global = carregar_logs_dia()
ranking_global = pd.DataFrame()
if not df_logs_global.empty:
    hoje = data_hoje()
    logs_hoje = df_logs_global[df_logs_global['DataHora'].astype(str).str.contains(hoje)]
    feitos_logs = logs_hoje[logs_hoje['Acao'].astype(str).str.contains("Finalizou", case=False)]
    if not feitos_logs.empty:
        ranking_global = feitos_logs['Usuario'].value_counts().reset_index()
        ranking_global.columns = ['Nome', 'Qtd']

# EFEITO BALÕES
if 'soltar_baloes' in st.session_state and st.session_state['soltar_baloes']:
    st.balloons()
    st.session_state['soltar_baloes'] = False

# ===================================================
# APLICADOR DE TEMAS
# ===================================================
if 'tema_escolhido' not in st.session_state:
    st.session_state['tema_escolhido'] = "Padrão"

if st.session_state['tema_escolhido'] == "Matrix":
    st.markdown("""
        <style>
        .stApp { background-color: #0D0D0D; }
        h1, h2, h3, h4, p, label, li { color: #00FF41 !important; font-family: 'Courier New', Courier, monospace !important; }
        [data-testid="stMetricValue"], [data-testid="stMetricLabel"] { color: #00FF41 !important; font-family: 'Courier New', Courier, monospace !important; }
        .stButton>button { background-color: #000000; color: #00FF41 !important; border: 1px solid #00FF41; box-shadow: 0 0 5px #00FF41; font-family: 'Courier New', Courier, monospace !important;}
        .stButton>button:hover { background-color: #00FF41; color: #000000 !important; }
        [data-testid="stSidebar"] { background-color: #050505; border-right: 2px solid #00FF41; }
        .stSelectbox>div>div { background-color: #000; color: #00FF41; border: 1px solid #00FF41; }
        .stTextInput>div>div>input { background-color: #000; color: #00FF41; border: 1px solid #00FF41; }
        </style>
    """, unsafe_allow_html=True)
elif st.session_state['tema_escolhido'] == "Escuro":
    st.markdown("""
        <style>
        .stApp { background-color: #0b1120; }
        h1, h2, h3, h4, p, label, li { color: #e2e8f0 !important; }
        [data-testid="stMetricValue"], [data-testid="stMetricLabel"] { color: #e2e8f0 !important; }
        .stButton>button { background-color: #1e293b; color: #38bdf8 !important; border: 1px solid #38bdf8; }
        .stButton>button:hover { background-color: #38bdf8; color: #0b1120 !important; }
        [data-testid="stSidebar"] { background-color: #0f172a; border-right: 1px solid #1e293b; }
        </style>
    """, unsafe_allow_html=True)
elif st.session_state['tema_escolhido'] == "Rosa":
    st.markdown("""
        <style>
        .stApp { background-color: #fff0f5; }
        h1, h2, h3, h4, p, label, li { color: #d81b60 !important; font-family: 'Trebuchet MS', sans-serif !important; }
        [data-testid="stMetricValue"], [data-testid="stMetricLabel"] { color: #d81b60 !important; font-family: 'Trebuchet MS', sans-serif !important; }
        .stButton>button { background-color: #ffb6c1; color: #fff !important; border: 2px solid #ff69b4; border-radius: 20px; font-family: 'Trebuchet MS', sans-serif !important;}
        .stButton>button:hover { background-color: #ff69b4; color: #fff !important; }
        [data-testid="stSidebar"] { background-color: #ffe4e1; border-right: 2px solid #ffb6c1; }
        .stSelectbox>div>div { background-color: #fff; color: #d81b60; border: 2px solid #ffb6c1; border-radius: 15px;}
        .stTextInput>div>div>input { background-color: #fff; color: #d81b60; border: 2px solid #ffb6c1; border-radius: 15px;}
        </style>
    """, unsafe_allow_html=True)

# --- TELA DE LOGIN ---
if 'usuario' not in st.session_state:
    st.title("🎫 ESTEIRA - QUALITOR")
    
    df_equipe = carregar_status_equipe()
    if not df_equipe.empty and 'Colaboradores' in df_equipe.columns:
        lista_nomes = [n for n in df_equipe['Colaboradores'].tolist() if str(n).strip() != '']
        senhas = dict(zip(df_equipe['Colaboradores'], df_equipe.get('Senha', ['']*len(df_equipe))))
    else:
        lista_nomes = []; senhas = {}
        st.warning("⚠️ Planilha carregando ou aba Colaboradores vazia. Clique abaixo se demorar.")
        if st.button("🔄 Recarregar Nomes"):
            st.cache_data.clear(); st.rerun()
    
    c1, c2 = st.columns(2)
    escolha = c1.selectbox("Usuário:", [""] + lista_nomes)
    senha = c2.text_input("Senha:", type="password")
    
    if st.button("Entrar no Sistema"):
        if escolha and str(senha) == str(senhas.get(escolha, "")):
            st.session_state['usuario'] = escolha
            st.session_state['tamanho_fila_anterior'] = 0 
            registrar_log(escolha, "LOGIN") 
            
            try:
                idx = df_equipe.index[df_equipe['Colaboradores'] == escolha].tolist()[0] + 2
                aba_users.update_cell(idx, 3, "Disponivel")
                st.cache_data.clear() 
            except: pass
            
            st.rerun()
        else: st.error("Dados inválidos.")

# --- SISTEMA LOGADO ---
else:
    usuario = st.session_state['usuario']
    df = carregar_dados_chamados()
    df_equipe = carregar_status_equipe()
    
    # 🎯 GPS DE COLUNAS
    if not df.empty:
        cols_planilha = df.columns.tolist()
        COL_STATUS = cols_planilha.index("Status") + 1 if "Status" in cols_planilha else 3
        COL_RESP = cols_planilha.index("Responsavel") + 1 if "Responsavel" in cols_planilha else 5
        COL_INICIO = cols_planilha.index("Inicio") + 1 if "Inicio" in cols_planilha else 6
        COL_FIM = cols_planilha.index("Data_Conclusao") + 1 if "Data_Conclusao" in cols_planilha else 7
    else:
        COL_STATUS, COL_RESP, COL_INICIO, COL_FIM = 3, 5, 6, 7
    
    # ===================================================
    # 📺 MODO TELÃO (TV DO SALÃO - SLA COMPLETO)
    # ===================================================
    if usuario == "TV":
        st.markdown("<h1 style='text-align: center; color: #1E90FF; font-size: 70px;'>📺 Dashboard Operacional</h1>", unsafe_allow_html=True)
        st.markdown(f"<p style='text-align: center; font-size: 25px;'>Última atualização: {hora_texto()}</p>", unsafe_allow_html=True)
        
        st.markdown("""
            <style>
            [data-testid="stSidebar"] {display: none;} 
            header {display: none;} 
            .block-container {padding-top: 1rem; max-width: 95%;}
            [data-testid="stMetricValue"] {font-size: 60px !important; line-height: 1.2;}
            [data-testid="stMetricLabel"] {font-size: 22px !important; font-weight: bold;}
            </style>
        """, unsafe_allow_html=True)

        if st.button("Sair (Logout da TV)"):
            del st.session_state['usuario']; st.rerun()
            
        # --- CÁLCULOS ---
        qtd_online = len(df_equipe[df_equipe['Status'] == 'Disponivel']) if not df_equipe.empty and 'Status' in df_equipe.columns else 0
        
        # 0. Base Geral
        total_base = len(df) if not df.empty else 0
        base_fora = 0
        base_dentro = 0
        if not df.empty and 'SLA' in df.columns:
            base_fora = len(df[df['SLA'].astype(str).str.lower().str.contains('fora')])
            base_dentro = total_base - base_fora
        else: base_dentro = total_base

        # 1. Pendentes
        pend_total = 0; pend_dentro = 0; pend_fora = 0
        if not df.empty:
            pend_df = df[df['Status'] == 'Pendente'].copy()
            pend_total = len(pend_df)
            if pend_total > 0 and 'SLA' in pend_df.columns:
                pend_fora = len(pend_df[pend_df['SLA'].astype(str).str.lower().str.contains('fora')])
                pend_dentro = pend_total - pend_fora
            else: pend_dentro = pend_total
                
        # 2. Em Andamento
        and_total = 0; and_dentro = 0; and_fora = 0
        if not df.empty:
            and_df = df[df['Status'] == 'Em Andamento'].copy()
            and_total = len(and_df)
            if and_total > 0 and 'SLA' in and_df.columns:
                and_fora = len(and_df[and_df['SLA'].astype(str).str.lower().str.contains('fora')])
                and_dentro = and_total - and_fora
            else: and_dentro = and_total
                
        # 3. Feitos Hoje
        feitos_total = 0; feitos_dentro = 0; feitos_fora = 0
        if not df.empty and 'Data_Conclusao' in df.columns:
            hoje = data_hoje()
            feitos_df = df[(df['Status'] == 'Concluido') & (df['Data_Conclusao'].astype(str).str.contains(hoje))].copy()
            feitos_total = len(feitos_df)
            if feitos_total > 0 and 'SLA' in feitos_df.columns:
                feitos_fora = len(feitos_df[feitos_df['SLA'].astype(str).str.lower().str.contains('fora')])
                feitos_dentro = feitos_total - feitos_fora
            else: feitos_dentro = feitos_total

        # --- EXIBIÇÃO NO TELÃO ---
        st.markdown(f"<h2 style='text-align: center;'>🟢 Operadores Online: {qtd_online}</h2>", unsafe_allow_html=True)
        st.write("---")
        
        st.markdown("### 🗄️ Base Geral (Todos os Status)")
        cb1, cb2, cb3 = st.columns(3)
        cb1.metric("Total na Base", total_base)
        cb2.metric("✅ SLA no Prazo", base_dentro)
        cb3.metric("🔥 SLA Fora do Prazo", base_fora)

        st.write("---")
        st.markdown("### 🎫 Fila de Espera (Pendentes)")
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Pendente", pend_total)
        c2.metric("✅ SLA no Prazo", pend_dentro)
        c3.metric("🔥 SLA Fora do Prazo", pend_fora)
        
        st.write("---")
        st.markdown("### ⚙️ Em Atendimento (Agora)")
        c4, c5, c6 = st.columns(3)
        c4.metric("Total em Andamento", and_total)
        c5.metric("✅ SLA no Prazo", and_dentro)
        c6.metric("🔥 SLA Fora do Prazo", and_fora)
        
        st.write("---")
        st.markdown("### 🏆 Fechamentos (Hoje)")
        c7, c8, c9 = st.columns(3)
        c7.metric("Total Concluído", feitos_total)
        c8.metric("🟢 SLA no Prazo", feitos_dentro)
        c9.metric("🔴 SLA Fora do Prazo", feitos_fora)
        
        time.sleep(15)
        st.cache_data.clear(); st.rerun()

    # ===================================================
    # 👨‍💻 VISÃO NORMAL (GERENTE OU OPERADOR)
    # ===================================================
    else:
        status_real = "Erro"
        linha_planilha = None
        minhas_etapas = ['Todas']
        
        if not df_equipe.empty and 'Colaboradores' in df_equipe.columns:
            meus_dados = df_equipe[df_equipe['Colaboradores'] == usuario]
            if not meus_dados.empty:
                status_real = meus_dados.iloc[0].get('Status', 'Erro')
                etapas_str = str(meus_dados.iloc[0].get('Etapas_Permitidas', 'Todas'))
                minhas_etapas = [e.strip() for e in etapas_str.split(',')]
                idx = df_equipe.index[df_equipe['Colaboradores'] == usuario].tolist()[0] + 2
                linha_planilha = idx

        with st.sidebar:
            st.header(f"👤 {usuario}")
            
            novo_tema = st.selectbox("🎨 Tema Visual", ["Padrão", "Matrix", "Escuro", "Rosa"], index=["Padrão", "Matrix", "Escuro", "Rosa"].index(st.session_state['tema_escolhido']))
            if novo_tema != st.session_state['tema_escolhido']:
                st.session_state['tema_escolhido'] = novo_tema
                st.rerun()
            st.divider()

            modo_gerente = False
            if usuario in ADMINS:
                st.success("👑 Modo Gestor Liberado")
                modo_gerente = st.toggle("Painel de Gestão", value=False)
                st.divider()
            
            st.info(f"Status Atual: **{status_real}**")
            
            c1, c2 = st.columns(2)
            if c1.button("🟢 Online"):
                if linha_planilha:
                    aba_users.update_cell(linha_planilha, 3, "Disponivel")
                    registrar_log(usuario, "Ficou Disponivel")
                    st.cache_data.clear(); st.rerun()
            if c2.button("☕ Pausa"):
                if linha_planilha:
                    aba_users.update_cell(linha_planilha, 3, "Pausa")
                    registrar_log(usuario, "Entrou em Pausa")
                    st.cache_data.clear(); st.rerun()
            if st.button("🚽 Banheiro"):
                if linha_planilha:
                    aba_users.update_cell(linha_planilha, 3, "Banheiro")
                    registrar_log(usuario, "Foi ao Banheiro")
                    st.cache_data.clear(); st.rerun()
            
            st.divider()
            st.subheader("🏆 Seu Desempenho")
            if not df.empty and 'Data_Conclusao' in df.columns:
                hoje = data_hoje()
                feitos_hoje = df[(df['Status'] == 'Concluido') & (df['Data_Conclusao'].astype(str).str.contains(hoje))]
                
                if not feitos_hoje.empty:
                    ranking = feitos_hoje['Responsavel'].value_counts().reset_index()
                    ranking.columns = ['Nome', 'Qtd']
                    minha_posicao = ranking.index[ranking['Nome'] == usuario].tolist()
                    
                    if minha_posicao:
                        pos_real = minha_posicao[0] + 1
                        qtd_minha = ranking.iloc[minha_posicao[0]]['Qtd']
                        st.markdown(f"✅ **Feitos hoje:** {qtd_minha} chamados")
                        if pos_real == 1: st.success(f"🥇 Você está em 1º Lugar na equipe!")
                        elif pos_real == 2: st.info(f"🥈 Você está em 2º Lugar na equipe!")
                        elif pos_real == 3: st.warning(f"🥉 Você está em 3º Lugar na equipe!")
                        else: st.markdown(f"📍 **Sua posição:** {pos_real}º Lugar")
                    else: st.caption("Você ainda não finalizou chamados hoje.")

                    if usuario in ADMINS:
                        st.write("---")
                        st.caption("👑 Visão do Gestor (Top 3):")
                        for i, row in ranking.head(3).iterrows():
                            medalha = "🥇" if i == 0 else "🥈" if i == 1 else "🥉"
                            st.markdown(f"{medalha} {row['Nome']} ({row['Qtd']})")
                else: st.caption("A corrida de hoje ainda não começou!")

            st.divider()
            if st.button("Sair (Logout)"):
                registrar_log(usuario, "LOGOUT") 
                del st.session_state['usuario']; st.rerun()

        # ===================================================
        # 👑 VISÃO DO GERENTE (ADMIN)
        # ===================================================
        if modo_gerente:
            st.title("📊 Painel de Controle - Gestão")
            st.caption(f"Última atualização: {hora_texto()}")
            if st.button("🔄 Atualizar Tudo"): st.cache_data.clear(); st.rerun()
            
            # ===================================================
            # 📄 GERADOR DE RELATÓRIO EXECUTIVO (PDF)
            # ===================================================
            st.write("---")
            st.subheader("📄 Relatório Executivo (PDF)")
            with st.expander("Gerar Fechamento do Turno em PDF"):
                st.write("Este robô cruza os dados do Log (Produção) com a Fila Atual para montar o documento oficial da Diretoria.")
                
                aviso_pdf = st.text_input("Observação da Gestão (Opcional):", placeholder="Ex: Foco nos atrasos da Transportadora X")
                
                if st.button("🖨️ Mapear Dados e Criar PDF", use_container_width=True):
                    with st.spinner("Compilando dados em tempo real..."):
                        
                        # 1. Função anti-bug para remover acentos (o PDF padrão não lê UTF-8 direito)
                        def formatar_texto(texto):
                            return unicodedata.normalize('NFKD', str(texto)).encode('ASCII', 'ignore').decode('ASCII')
                        
                        # 2. Captura dos KPIs Atuais
                        pendentes_totais = len(df[df['Status'] == 'Pendente']) if not df.empty else 0
                        prioridade_df = df[(df['Status'] == 'Pendente') & (df['SLA'].astype(str).str.contains('Prioridade', case=False))] if not df.empty and 'SLA' in df.columns else pd.DataFrame()
                        prioridade_1_totais = len(prioridade_df)
                        feitos_totais = ranking_global['Qtd'].sum() if not ranking_global.empty else 0
                        
                        # 3. Desenho do Documento PDF
                        pdf = FPDF()
                        pdf.add_page()
                        
                        # Cabeçalho
                        pdf.set_font('Arial', 'B', 16)
                        pdf.cell(0, 10, formatar_texto('Relatorio Executivo - Esteira Qualitor'), 0, 1, 'C')
                        pdf.set_font('Arial', 'I', 10)
                        pdf.cell(0, 10, formatar_texto(f'Gerado pelo Sistema Automatizado em: {hora_texto()}'), 0, 1, 'C')
                        pdf.ln(10)
                        
                        # Seção 1: Volume e Crise
                        pdf.set_font('Arial', 'B', 12)
                        pdf.cell(0, 10, formatar_texto('1. PANORAMA OPERACIONAL (Fila vs Producao)'), 0, 1)
                        pdf.set_font('Arial', '', 12)
                        pdf.cell(0, 10, formatar_texto(f'> Chamados Finalizados Hoje: {feitos_totais} chamados resolvidos.'), 0, 1)
                        pdf.cell(0, 10, formatar_texto(f'> Fila de Espera Atual: {pendentes_totais} pendentes na esteira.'), 0, 1)
                        
                        # Alerta Crítico
                        if prioridade_1_totais > 0:
                            pdf.set_text_color(255, 0, 0) # Cor Vermelha
                            pdf.cell(0, 10, formatar_texto(f'> ALERTA DE SLA: {prioridade_1_totais} chamados de Prioridade 1 (Vencem Hoje) na fila!'), 0, 1)
                            pdf.set_text_color(0, 0, 0) # Volta para Preto
                        else:
                            pdf.set_text_color(0, 128, 0)
                            pdf.cell(0, 10, formatar_texto('> ALERTA DE SLA: Nenhum chamado de Prioridade Maxima pendente. Operacao controlada.'), 0, 1)
                            pdf.set_text_color(0, 0, 0)
                        pdf.ln(5)
                        
                        # Seção 2: Ranking
                        pdf.set_font('Arial', 'B', 12)
                        pdf.cell(0, 10, formatar_texto('2. DESTAQUES DA EQUIPE (Top 3 Produtividade)'), 0, 1)
                        pdf.set_font('Arial', '', 12)
                        if not ranking_global.empty:
                            for i, row in ranking_global.head(3).iterrows():
                                pdf.cell(0, 10, formatar_texto(f"{i+1} Lugar: {row['Nome']} - {row['Qtd']} concluidos"), 0, 1)
                        else:
                            pdf.cell(0, 10, formatar_texto('A equipe ainda nao finalizou chamados nesta rodada.'), 0, 1)
                        pdf.ln(5)
                        
                        # Seção 3: Mensagem da Gestão
                        if aviso_pdf:
                            pdf.set_font('Arial', 'B', 12)
                            pdf.cell(0, 10, formatar_texto('3. DIRETRIZ DA GESTAO'), 0, 1)
                            pdf.set_font('Arial', 'I', 11)
                            pdf.multi_cell(0, 10, formatar_texto(aviso_pdf))
                            
                        # 4. Finalização e Download
                        nome_arquivo = f"Fechamento_SAC_{datetime.now().strftime('%d%m%Y_%H%M')}.pdf"
                        pdf.output(nome_arquivo, 'F')
                        
                        with open(nome_arquivo, "rb") as f:
                            bytes_pdf = f.read()
                            
                        st.success("✅ PDF Gerado com Sucesso!")
                        st.download_button(
                            label="📥 BAIXAR RELATÓRIO PDF AGORA",
                            data=bytes_pdf,
                            file_name=nome_arquivo,
                            mime="application/pdf",
                            type="primary",
                            use_container_width=True
                        )

            st.write("---")
            st.subheader("🚨 Monitoramento de SLA (Em Andamento)")
            em_andamento = pd.DataFrame()
            if not df.empty:
                em_andamento = df[df['Status'] == 'Em Andamento'].copy()
                if not em_andamento.empty:
                    lista_sla = []
                    for index, row in em_andamento.iterrows():
                        status_visual = row.get('SLA', 'Sem Info')
                        if 'fora' in str(status_visual).lower():
                            status_visual = f"🔥 {status_visual}"
                        else:
                            status_visual = f"✅ {status_visual}"
                            
                        lista_sla.append({
                            "Chamado": row.get('Dados', ''),
                            "Etapa": row.get('Etapa', ''),
                            "Responsável": row.get('Responsavel', ''),
                            "SLA": status_visual,
                            "ID": row.get('ID')
                        })
                    df_sla = pd.DataFrame(lista_sla)
                    st.dataframe(df_sla, hide_index=True, use_container_width=True)
                else: st.success("Equipe livre! Nenhum chamado em andamento.")

            st.write("---")
            st.subheader("🛠️ Ações de Emergência")
            if not em_andamento.empty:
                opcoes = em_andamento.apply(lambda x: f"L{x.name + 2} - ID {x['ID']} - {x['Dados']} ({x['Responsavel']})", axis=1).tolist()
                selecionado = st.selectbox("Selecione um chamado travado:", [""] + opcoes)
                
                if selecionado:
                    linha_trava = int(selecionado.split(" - ")[0].replace("L", ""))
                    col_dev, col_forcar = st.columns(2)

                    if col_dev.button("↩️ Devolver para Fila"):
                        try:
                            aba_chamados.update_cell(linha_trava, COL_STATUS, "Pendente") 
                            aba_chamados.update_cell(linha_trava, COL_RESP, "") 
                            aba_chamados.update_cell(linha_trava, COL_INICIO, "") 
                            registrar_log(usuario, f"ADMIN: Devolveu linha {linha_trava}")
                            st.success("Devolvido!")
                            st.cache_data.clear(); time.sleep(1); st.rerun()
                        except Exception as e: st.error(f"Erro: {e}")

                    if col_forcar.button("🏁 Forçar Conclusão"):
                        try:
                            aba_chamados.update_cell(linha_trava, COL_STATUS, "Concluido") 
                            aba_chamados.update_cell(linha_trava, COL_FIM, hora_texto()) 
                            registrar_log(usuario, f"ADMIN: Forçou conclusão linha {linha_trava}")
                            st.success("Encerrado!")
                            st.cache_data.clear(); time.sleep(1); st.rerun()
                        except Exception as e: st.error(f"Erro: {e}")
            else: st.info("Nada para destravar.")

            st.write("---")
            c_status, c_prod = st.columns(2)
            with c_status:
                st.subheader("🚦 Equipe Online")
                if not df_equipe.empty:
                    cols = [c for c in df_equipe.columns if c in ['Colaboradores','Status']]
                    st.dataframe(df_equipe[cols], hide_index=True, use_container_width=True)
            with c_prod:
                st.subheader("🏆 Produção Hoje (Log Real)")
                if not ranking_global.empty:
                    st.dataframe(ranking_global, hide_index=True, use_container_width=True)
                else: st.info("Sem dados hoje.")

        # ===================================================
        # 👷 VISÃO DO OPERADOR
        # ===================================================
        else:
            if status_real != "Disponivel":
                st.warning(f"⚠️ **VOCÊ ESTÁ EM PAUSA ({status_real})**")
            else:
                st.success("🟢 ONLINE - Aguardando chamados...")
                
                if df.empty:
                    st.write("Sem dados na esteira.")
                    if st.button("Recarregar"): st.cache_data.clear(); st.rerun()
                else:
                    meu_chamado = df[(df['Status'] == 'Em Andamento') & (df['Responsavel'] == usuario)]
                    
                    if len(meu_chamado) > 0:
                        if len(meu_chamado) > 1:
                            st.warning("⚠️ Atenção: Você tem mais de um chamado em andamento. Finalize este primeiro.")

                        dados = meu_chamado.iloc[0]
                        num = dados.get('Dados', 'N/A') 
                        etapa_atual = dados.get('Etapa', 'N/A')
                        sla_atual = str(dados.get('SLA', 'Sem Info'))
                        
                        if 'fora' in sla_atual.lower():
                            st.error(f"🔥 ALERTA DE SLA: {sla_atual}")
                        else:
                            st.info(f"✅ Status do SLA: {sla_atual}")

                        st.markdown(f"### 📞 Chamado: **{num}** | Etapa: **{etapa_atual}**")
                        if str(num) != 'N/A':
                            link = f"https://frigelar.qualitorsoftware.com/html/hd/hdchamado/cadastro_chamado.php?cdchamado={num}"
                            st.link_button("🔗 Abrir no Qualitor", link)
                        
                        # 🧠 RESUMIDOR DE IA
                        with st.expander("✨ Resumir Histórico do Chamado (Inteligência Artificial)"):
                            historico = st.text_area("Cole aqui os assentamentos do cliente para análise rápida:", height=100)
                            if st.button("Mastigar Histórico"):
                                if ia_ativa and historico:
                                    with st.spinner("A processar os dados..."):
                                        prompt = f"Analise este histórico de atendimento e devolva os 3 pontos mais importantes (causa, situação atual e o que o cliente quer). Seja extremamente resumido:\n\n{historico}"
                                        try:
                                            resp = modelo_oraculo.generate_content(prompt)
                                            st.info(resp.text)
                                        except Exception as e:
                                            st.error(f"Erro na IA: {e}")
                                elif not ia_ativa:
                                    st.error("IA desligada. Verifique a chave de API.")
                                else:
                                    st.warning("Cole o texto primeiro.")

                        st.write("---")
                        if 'confirmar' not in st.session_state: st.session_state['confirmar'] = False
                        
                        if not st.session_state['confirmar']:
                            if st.button("✅ FINALIZAR", type="primary"):
                                st.session_state['confirmar'] = True; st.rerun()
                        else:
                            st.warning("Confirma?")
                            cy, cn = st.columns(2)
                            if cy.button("👍 SIM"):
                                try:
                                    idx_linha = int(meu_chamado.index[0]) + 2 
                                    aba_chamados.update_cell(idx_linha, COL_STATUS, "Concluido") 
                                    aba_chamados.update_cell(idx_linha, COL_FIM, hora_texto()) 
                                    registrar_log(usuario, f"Finalizou {num}")
                                    st.session_state['confirmar'] = False
                                    
                                    hoje = data_hoje()
                                    if 'Data_Conclusao' in df.columns:
                                        feitos = len(df[(df['Status'] == 'Concluido') & (df['Responsavel'] == usuario) & (df['Data_Conclusao'].astype(str).str.contains(hoje))])
                                    else:
                                        feitos = 0
                                        
                                    if (feitos + 1) in [10, 25, 50, 100]:
                                        st.session_state['soltar_baloes'] = True
                                        
                                    st.cache_data.clear(); time.sleep(1); st.rerun()
                                except Exception as e: st.error(f"Erro ao salvar: {e}")
                            
                            if cn.button("❌ NÃO"):
                                st.session_state['confirmar'] = False; st.rerun()
                    
                    else:
                        fila = df[(df['Status'] == 'Pendente') & (df['Responsavel'] == "")].copy()
                        if "Todas" not in minhas_etapas and "todas" not in [e.lower() for e in minhas_etapas]:
                            fila = fila[fila['Etapa'].astype(str).isin(minhas_etapas)]

                        qtd = len(fila)
                        
                        if 'tamanho_fila_anterior' not in st.session_state:
                            st.session_state['tamanho_fila_anterior'] = qtd
                            
                        if qtd > st.session_state['tamanho_fila_anterior']:
                            st.toast("🔔 Novo chamado entrou na fila!")
                            som_url = f"https://assets.mixkit.co/active_storage/sfx/2869/2869-preview.mp3?t={time.time()}"
                            st.markdown(f'<audio autoplay="true"><source src="{som_url}" type="audio/mpeg"></audio>', unsafe_allow_html=True)
                        
                        st.session_state['tamanho_fila_anterior'] = qtd 
                        
                        c_f, c_r = st.columns([3,1])
                        c_f.metric("Sua Fila de Espera (Permitida)", qtd)
                        if c_r.button("🔄 Atualizar Fila"): st.cache_data.clear(); st.rerun()
                        
                        if qtd > 0:
                            if st.button("📥 PEGAR PRÓXIMO", type="primary", use_container_width=True):
                                try:
                                    if not fila.empty:
                                        if 'SLA' in fila.columns:
                                            fila['Peso_SLA'] = fila['SLA'].astype(str).apply(lambda x: 1 if 'fora' in x.lower() else 2)
                                            fila = fila.sort_values(by='Peso_SLA', kind='stable')
                                        
                                        item = fila.iloc[0]
                                        num_l = str(item.get('Dados','')).replace('.0','')
                                        idx_linha = int(item.name) + 2 
                                        agora = hora_texto()
                                        
                                        aba_chamados.update_cell(idx_linha, COL_STATUS, "Em Andamento") 
                                        aba_chamados.update_cell(idx_linha, COL_RESP, usuario)        
                                        aba_chamados.update_cell(idx_linha, COL_INICIO, agora)          
                                        registrar_log(usuario, f"Pegou {num_l} (Etapa {item.get('Etapa','')})")
                                        
                                        st.cache_data.clear(); time.sleep(1); st.rerun()
                                    else: st.warning("Alguém pegou antes!"); time.sleep(1); st.rerun()
                                except Exception as e: st.error(f"Erro ao pegar chamado: {e}")
                        else: 
                            st.caption("Sem chamados na sua alçada. A fila será verificada automaticamente a cada 60 segundos.")
                            # 🔄 ATUALIZAÇÃO SILENCIOSA (SEM JOGUINHO, 100% PRODUÇÃO)
                            st_autorefresh(interval=60000, limit=None, key="refresh_fila_vazia")

            # --- HISTÓRICO ---
            st.write("---")
            if not df.empty:
                hist = df[(df['Status']=='Concluido') & (df['Responsavel']==usuario)].copy()
                if not hist.empty and 'Data_Conclusao' in hist.columns:
                    hoje = data_hoje()
                    hist_hoje = hist[hist['Data_Conclusao'].astype(str).str.contains(hoje)].copy()
                    qtd_hoje = len(hist_hoje)
                    st.subheader(f"✅ Seus Concluídos Hoje: **{qtd_hoje}**")
                    
                    if qtd_hoje > 0:
                        hist_hoje['Link'] = "https://frigelar.qualitorsoftware.com/html/hd/hdchamado/cadastro_chamado.php?cdchamado=" + hist_hoje['Dados'].astype(str)
                        hist_hoje['Tempo_Gasto'] = hist_hoje.apply(lambda row: calcular_duracao_str(row.get('Inicio', ''), row.get('Data_Conclusao', '')), axis=1)
                        hist_hoje = hist_hoje.rename(columns={'Data_Conclusao': 'Horário'})
                        cols_show = ['Link', 'Etapa', 'SLA', 'Tempo_Gasto', 'Horário'] if 'SLA' in hist_hoje.columns else ['Link', 'Etapa', 'Tempo_Gasto', 'Horário']
                        st.dataframe(hist_hoje[cols_show].tail(15), hide_index=True, use_container_width=True,
                            column_config={"Link": st.column_config.LinkColumn("Chamado", display_text=r"cdchamado=(.*)")})
                    else: st.caption("Nenhum chamado concluído por você hoje, ainda. Vamos lá!")

        # ===================================================
        # 🧙‍♂️ GAVETA DO ORÁCULO (INTELIGÊNCIA ARTIFICIAL)
        # ===================================================
        st.write("---")
        with st.expander("🧙‍♂️ Oráculo Frigelar - Tire suas dúvidas da operação"):
            pergunta = st.text_input("O que você precisa saber?", placeholder="Ex: Qual o prazo de devolução?")
            
            if st.button("✨ Perguntar ao Oráculo"):
                if ia_ativa:
                    try:
                        with open("regras_operacao.txt", "r", encoding="utf-8") as f:
                            texto_regras = f.read()
                        
                        with st.spinner("O Oráculo está consultando o manual..."):
                            comando = f"""
                            Você é o Oráculo, um assistente interno sênior do SAC da Frigelar.
                            Responda a pergunta do operador baseando-se EXCLUSIVAMENTE no manual abaixo.
                            Se a resposta NÃO estiver no manual, diga exatamente: "Desculpe, não encontrei essa informação nas minhas regras atuais. Consulte o Supervisor."
                            Seja direto, claro e educado.

                            MANUAL DA OPERAÇÃO:
                            {texto_regras}

                            PERGUNTA DO OPERADOR: {pergunta}
                            """
                            
                            resposta = modelo_oraculo.generate_content(comando)
                            st.info(resposta.text)
                    
                    except FileNotFoundError:
                        st.error("🚨 Arquivo 'regras_operacao.txt' não encontrado. Crie o arquivo na mesma pasta do sistema e lembre de subir ele para o GitHub também!")
                    except Exception as e:
                        erro_str = str(e)
                        if "429" in erro_str or "Quota" in erro_str:
                            st.warning("⏳ Estou respondendo a muitos operadores ao mesmo tempo! Por favor, aguarde 1 minutinho e tente perguntar de novo.")
                        else:
                            st.error(f"🚨 Erro ao processar a resposta: {erro_str}")
                else:
                    st.error(f"🚨 IA não configurada corretamente. Verifique a chave no secrets.toml. Erro: {erro_ia}")

        # ===================================================
        # 🚚 GAVETA DE TRANSPORTADORAS
        # ===================================================
        st.write("---")
        with st.expander("🚚 Agenda de Contatos - Transportadoras"):
            df_transp = carregar_agenda_transp()
            if not df_transp.empty and 'Transportadora' in df_transp.columns:
                lista_t = [str(t) for t in df_transp['Transportadora'].dropna().unique() if str(t).strip() != '']
                escolha_t = st.selectbox("Selecione a Transportadora:", [""] + sorted(lista_t))
                if escolha_t:
                    dados_t = df_transp[df_transp['Transportadora'] == escolha_t].iloc[0]
                    email_t = dados_t.get('Email_Transp', 'Não cadastrado')
                    email_l = dados_t.get('Email_Logistica', 'Não cadastrado')
                    
                    c_t, c_l = st.columns(2)
                    with c_t:
                        st.caption("E-mail Transportadora (Clique na caixa para copiar):")
                        st.code(email_t, language="text") 
                    with c_l:
                        st.caption("E-mail Logística (Clique na caixa para copiar):")
                        st.code(email_l, language="text")
            else: st.info("Aba 'Transportadoras' não encontrada ou ainda está vazia.")

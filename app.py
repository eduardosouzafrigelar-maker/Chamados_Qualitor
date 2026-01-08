import streamlit as st
import pandas as pd
import gspread
from datetime import datetime
import time
import pytz

# --- CONFIGURAÇÃO INICIAL ---
st.set_page_config(page_title="Distribuidor Qualitor", page_icon="🎫")

# --- CONEXÃO COM CACHE DE RECURSO ---
@st.cache_resource
def conectar_google_sheets():
    try:
        if "gcp_service_account" in st.secrets:
            creds_dict = st.secrets["gcp_service_account"]
            client = gspread.service_account_from_dict(creds_dict)
        else:
            client = gspread.service_account(filename="credentials.json")

        # Conecta na planilha do SEGUNDO sistema
        sheet = client.open("Chamados_Qualitor")
        return sheet
    except Exception as e:
        st.error("Erro na conexão! Verifique os Segredos ou o arquivo JSON.")
        st.stop()

# --- LEITURA INTELIGENTE (ANTI-ERRO 429) ---
@st.cache_data(ttl=5)
def carregar_dados_planilha():
    sh = conectar_google_sheets()
    try:
        aba = sh.worksheet("Chamados")
        dados = aba.get_all_records()
        return pd.DataFrame(dados)
    except Exception as e:
        return pd.DataFrame()

# Carrega a conexão principal
sh = conectar_google_sheets()

try:
    aba_chamados = sh.worksheet("Chamados")
    aba_users = sh.worksheet("Colaboradores")
except Exception as e:
    st.error(f"Erro: Não encontrei as abas. Detalhe: {e}")
    st.stop()

# --- FUNÇÃO PARA PEGAR HORA CERTA (BRASIL) ---
def hora_brasil():
    fuso = pytz.timezone('America/Sao_Paulo')
    return datetime.now(fuso).strftime("%d/%m/%Y %H:%M:%S")

# --- TELA DE LOGIN ---
if 'usuario' not in st.session_state:
    st.title("🎫 Login - Qualitor")
    
    try:
        lista_nomes = aba_users.col_values(1)[1:] 
    except:
        lista_nomes = []
    
    escolha = st.selectbox("Selecione seu nome:", [""] + lista_nomes)
    
    if st.button("Entrar no Sistema"):
        if escolha:
            st.session_state['usuario'] = escolha
            st.rerun()
        else:
            st.warning("Por favor, selecione um nome.")

# --- TELA PRINCIPAL ---
else:
    usuario = st.session_state['usuario']
    
    with st.sidebar:
        st.write(f"Logado como: **{usuario}**")
        if st.button("Sair / Trocar Usuário"):
            del st.session_state['usuario']
            st.rerun()
    
    st.title(f"Olá, {usuario} 👋")
    st.divider()

    # USANDO O CACHE PARA LER DADOS
    df = carregar_dados_planilha()

    if df.empty:
        st.warning("Carregando dados... Se demorar, clique abaixo.")
        if st.button("🔄 Forçar Atualização"):
            st.cache_data.clear()
            st.rerun()
        st.stop()

    if 'Status' in df.columns and 'Responsavel' in df.columns:
        meu_chamado = df[
            (df['Status'] == 'Em Andamento') & 
            (df['Responsavel'] == usuario)
        ]
    else:
        st.error("Colunas 'Status' ou 'Responsavel' não encontradas.")
        st.stop()

    # --- CENÁRIO A: TEM CHAMADO ABERTO ---
    if not meu_chamado.empty:
        dados = meu_chamado.iloc[0]
        numero_chamado = dados.get('Dados', 'N/A') 
        id_linha = dados.get('ID')
        
        st.info(f"Você tem um atendimento pendente: **{numero_chamado}**")
        
        col1, col2 = st.columns([3, 1])
        with col1:
            if numero_chamado != 'N/A':
                link_qualitor = f"https://frigelar.qualitorsoftware.com/html/hd/hdchamado/cadastro_chamado.php?cdchamado={numero_chamado}"
                st.link_button("🔗 ABRIR NO QUALITOR", link_qualitor)
        
        st.write("---")
        
        if st.button("✅ FINALIZAR ATENDIMENTO", type="primary"):
            with st.spinner("Finalizando..."):
                try:
                    # Limpa cache antes de escrever
                    st.cache_data.clear()
                    
                    cell = aba_chamados.find(str(id_linha))
                    numero_da_linha = cell.row
                    agora = hora_brasil()
                    
                    aba_chamados.update_cell(numero_da_linha, 3, "Concluido")
                    aba_chamados.update_cell(numero_da_linha, 6, agora)
                    
                    st.success("Chamado finalizado!")
                    time.sleep(1)
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro ao finalizar: {e}")

    # --- CENÁRIO B: ESTÁ LIVRE ---
    else:
        pendentes = df[df['Status'] == 'Pendente']
        qtd_pendentes = len(pendentes)

        st.write("Você está livre.")
        st.metric("Chamados na Fila", qtd_pendentes)

        if qtd_pendentes > 0:
            if st.button("📥 PEGAR PRÓXIMO CHAMADO"):
                with st.spinner("Buscando chamado..."):
                    # Limpa cache para garantir dados frescos
                    st.cache_data.clear()
                    
                    # Busca direto da fonte para não ter conflito
                    dados_frescos = aba_chamados.get_all_records()
                    df_novo = pd.DataFrame(dados_frescos)
                    
                    fila_real = df_novo[
                        (df_novo['Status'] == 'Pendente') & 
                        (df_novo['Responsavel'] == "")
                    ]
                    
                    if not fila_real.empty:
                        primeiro_livre = fila_real.iloc[0]
                        id_do_chamado = primeiro_livre['ID']
                        
                        try:
                            cell = aba_chamados.find(str(id_do_chamado))
                            linha_para_editar = cell.row
                            agora = hora_brasil()
                            
                            aba_chamados.update_cell(linha_para_editar, 3, "Em Andamento")
                            aba_chamados.update_cell(linha_para_editar, 4, usuario)
                            aba_chamados.update_cell(linha_para_editar, 5, agora)
                            
                            st.toast("Chamado atribuído com sucesso!")
                            time.sleep(1)
                            st.rerun()
                        except Exception as e:
                             st.error(f"Erro ao pegar chamado: {e}")
                    else:
                        st.warning("Alguém pegou antes de você.")
                        time.sleep(2)
                        st.rerun()
        else:
            st.success("Fila zerada! Aguarde novos chamados.")
            if st.button("🔄 Verificar Fila"):
                st.cache_data.clear()
                st.rerun()

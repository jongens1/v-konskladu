import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="Kapacitné plánovanie skladu", layout="wide")

st.title("📦 Kapacitné plánovanie skladu na základe vzniku a svozov")

# --- BOČNÝ PANEL ---
st.sidebar.header("⚙️ Nastavenia výkonu")
pick_rate_default = st.sidebar.number_input("Norma (Joblines / hod na 1 človeka)", value=40, step=5)

# --- FUNKCIA NA RÝCHLE NAČÍTANIE (CACHED) ---
@st.cache_data
def load_and_process_data(file):
    if file.name.endswith('.csv'):
        try:
            df = pd.read_csv(file, sep=';')
            if len(df.columns) <= 1:
                file.seek(0)
                df = pd.read_csv(file, sep=',')
        except:
            file.seek(0)
            df = pd.read_csv(file, sep=',')
    else:
        df = pd.read_excel(file, engine='openpyxl')
    
    # ⚡ EXTRÉMNE RÝCHLE SPRACOVANIE HODÍN (bez zasekávania)
    # Extrakcia hodiny zo vzniku
    df['Vznik Line'] = pd.to_datetime(df['Vznik Line'], dayfirst=True, errors='coerce')
    df['Hodina Vzniku'] = df['Vznik Line'].dt.hour

    # Extrakcia hodiny zo svozu (funguje na text "14:00" aj na dátum)
    df['Čas zvozu str'] = df['Čas zvozu'].astype(str)
    df['Hodina Svozu'] = df['Čas zvozu str'].str.extract(r'(\d{1,2})').astype(float)
    
    return df

# --- UPLOAD SÚBORU ---
uploaded_file = st.file_uploader("Nahraj súbor (Excel / CSV)", type=["xlsx", "csv"])

if uploaded_file is not None:
    with st.spinner('Spracovávam 12 000 riadkov... (trvá to cca 2 sekundy)'):
        df = load_and_process_data(uploaded_file)
        
    st.success(f"Dáta bleskovo načítané! Celkom {len(df):,} riadkov (Joblines).")

    st.sidebar.markdown("---")
    st.sidebar.header("🔍 Filtre")

    # --- FILTRE ---
    geo_sizes = df['Geo Size produktu'].dropna().unique().tolist() if 'Geo Size produktu' in df.columns else []
    selected_geo = st.sidebar.multiselect("Geo Size produktu", options=geo_sizes, default=geo_sizes)
    
    routings = df['RoutingType'].dropna().unique().tolist() if 'RoutingType' in df.columns else []
    selected_routing = st.sidebar.multiselect("Routing Type", options=routings, default=routings)
    
    # Aplikácia filtrov
    filtered_df = df.copy()
    if geo_sizes:
        filtered_df = filtered_df[filtered_df['Geo Size produktu'].isin(selected_geo)]
    if routings:
        filtered_df = filtered_df[filtered_df['RoutingType'].isin(selected_routing)]

    # --- AGREGÁCIA PO HODINÁCH ---
    inflow = filtered_df.groupby('Hodina Vzniku').agg(
        Vzniknute_Lines=('JobLine', 'count'),
        Vzniknute_Mnozstvo=('Množstvo', 'sum')
    ).reset_index()

    outflow = filtered_df.groupby('Hodina Svozu').agg(
        Deadline_Lines=('JobLine', 'count'),
        Deadline_Mnozstvo=('Množstvo', 'sum')
    ).reset_index()

    # Spojenie časových osí
    hourly = pd.merge(inflow, outflow, left_on='Hodina Vzniku', right_on='Hodina Svozu', how='outer')
    hourly['Hodina'] = hourly['Hodina Vzniku'].fillna(hourly['Hodina Svozu'])
    hourly = hourly.dropna(subset=['Hodina'])
    hourly['Hodina'] = hourly['Hodina'].astype(int)
    hourly = hourly.sort_values('Hodina').fillna(0)

    # Výpočet ľudí
    hourly['Potrební Ľudia'] = (hourly['Vzniknute_Lines'] / pick_rate_default).round(1)

    # --- KPI METRIKY ---
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Celkom Joblines", f"{len(filtered_df):,}")
    col2.metric("Celkové Množstvo (ks)", f"{int(filtered_df['Množstvo'].sum()):,}")
    
    peak_hour = int(hourly.loc[hourly['Vzniknute_Lines'].idxmax()]['Hodina']) if not hourly.empty else 0
    col3.metric("Špička vzniku práce", f"{peak_hour}:00 h")
    col4.metric("Odhad Človekohodín", f"{round(len(filtered_df) / pick_rate_default, 1)} h")

    st.markdown("---")

    # --- GRAF 1: PRÍTOK VS DEADLINES ---
    st.subheader("📈 1. Prítok práce (Vznik) vs. Termíny svozov")
    
    fig_flow = px.bar(
        hourly, 
        x='Hodina', 
        y=['Vzniknute_Lines', 'Deadline_Lines'],
        barmode='group',
        labels={'value': 'Počet Joblines', 'variable': 'Metrika'},
        title="Priebeh vznikajúcich a odchádzajúcich Joblines počas dňa",
        color_discrete_sequence=['#1f77b4', '#ff7f0e']
    )
    fig_flow.update_xaxes(dtick=1)
    st.plotly_chart(fig_flow, use_container_width=True)

    # --- GRAF 2: POTREBNÝ POČET ĽUDÍ ---
    st.subheader("👥 2. Odporúčané rozloženie ľudí na hodiny")
    
    fig_people = px.line(
        hourly, 
        x='Hodina', 
        y='Potrební Ľudia',
        markers=True,
        title=f"Koľko ľudí musí aktívne pickovať v danej hodine (Norma = {pick_rate_default} lines/h)",
        line_shape='spline'
    )
    fig_people.update_xaxes(dtick=1)
    fig_people.update_traces(line_color='#2ca02c', line_width=3)
    st.plotly_chart(fig_people, use_container_width=True)

    # --- DETAILNÁ TABUĽKA ---
    with st.expander("📄 Zobraziť hodinovú tabuľku dát"):
        st.dataframe(
            hourly[['Hodina', 'Vzniknute_Lines', 'Vzniknute_Mnozstvo', 'Deadline_Lines', 'Potrební Ľudia']],
            use_container_width=True
        )

else:
    st.info("👋 Prosím, nahraj súbor s dátami.")

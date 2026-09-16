import streamlit as st
import pandas as pd
import plotly.express as px

st.set_page_config(page_title="Kapacitné plánovanie skladu", layout="wide")

st.title("📦 Kapacitné plánovanie skladu na základe vzniku a svozov")

# --- BOČNÝ PANEL (FILTRE A NORMY) ---
st.sidebar.header("⚙️ Nastavenia výkonu (Normy)")

# Nastavenie noriem (Pick Rate) podľa veľkosti tovaru alebo všeobecne
pick_rate_default = st.sidebar.number_input("Všeobecná norma (Joblines / hod na 1 človeka)", value=40, step=5)

st.sidebar.markdown("---")
st.sidebar.header("🔍 Filtre")

# --- NAČÍTANIE DÁT ---
uploaded_file = st.file_uploader("Nahraj súbor z pondelka (Excel / CSV)", type=["xlsx", "csv"])

if uploaded_file:
    # Načítanie
    if uploaded_file.name.endswith('.csv'):
        df = pd.read_csv(uploaded_file)
    else:
        df = pd.read_excel(uploaded_file)
        
    st.success(f"Dáta načítané! Celkom {len(df)} riadkov (Joblines).")

    # Spracovanie časov
    # Prevádzame Vznik Line a Čas zvozu na datetime
    df['Vznik Line'] = pd.to_datetime(df['Vznik Line'], errors='coerce')
    
    # Ak Čas zvozu obsahuje iba čas (napr. 14:00:00), skombinujeme alebo vytiahneme hodinu
    df['Čas zvozu dt'] = pd.to_datetime(df['Čas zvozu'].astype(str), errors='coerce')
    
    df['Hodina Vzniku'] = df['Vznik Line'].dt.hour
    df['Hodina Svozu'] = df['Čas zvozu dt'].dt.hour
    
    # Ak sa čas zvozu nenačítal správne ako datetime, odchytíme to cez string extractor
    if df['Hodina Svozu'].isnull().all():
        df['Hodina Svozu'] = df['Čas zvozu'].astype(str).str.extract(r'(\d{1,2})').astype(float)

    # --- INTERAKTÍVNE FILTRE ---
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

    # --- AGREGÁCIA DÁT PO HODINÁCH ---
    inflow = filtered_df.groupby('Hodina Vzniku').agg(
        Vzniknute_Lines=('JobLine', 'count'),
        Vzniknute_Mnozstvo=('Množstvo', 'sum')
    ).reset_index()

    outflow = filtered_df.groupby('Hodina Svozu').agg(
        Deadline_Lines=('JobLine', 'count'),
        Deadline_Mnozstvo=('Množstvo', 'sum')
    ).reset_index()

    # Spojenie prítoku a svozov do jednej časovej osi
    hourly = pd.merge(inflow, outflow, left_on='Hodina Vzniku', right_on='Hodina Svozu', how='outer')
    hourly['Hodina'] = hourly['Hodina Vzniku'].fillna(hourly['Hodina Svozu']).astype(int)
    hourly = hourly.sort_values('Hodina').fillna(0)

    # Výpočet potreby ľudí
    hourly['Potrební Ľudia (Podľa Lines)'] = (hourly['Vzniknute_Lines'] / pick_rate_default).round(1)

    # --- ZOBRAZENIE KPI ---
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Celkom Joblines", f"{len(filtered_df):,}")
    col2.metric("Celkové Množstvo (ks)", f"{filtered_df['Množstvo'].sum():,}")
    col3.metric("Najvyťaženejšia hodina (Vznik)", f"{int(hourly.loc[hourly['Vzniknute_Lines'].idxmax()]['Hodina'])}:00")
    col4.metric("Odhad potreby Človekohodín", f"{round(len(filtered_df) / pick_rate_default, 1)} hod")

    st.markdown("---")

    # --- GRAF 1: PRÍTOK VS DEADLINES ---
    st.subheader("📈 1. Prítok práce (Vznik) vs. Termíny svozov (Deadlines)")
    
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
        y='Potrební Ľudia (Podľa Lines)',
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
            hourly[['Hodina', 'Vzniknute_Lines', 'Vzniknute_Mnozstvo', 'Deadline_Lines', 'Potrební Ľudia (Podľa Lines)']],
            use_container_width=True
        )

else:
    st.info("👋 Pre začiatok nahraj Excel súbor v hornom poli.")

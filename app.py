import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px

st.set_page_config(page_title="Kapacitné plánovanie skladu", layout="wide")

st.title("📦 Plánovanie kapacít skladu podľa Limitov nanesenia")

# --- BOČNÝ PANEL ---
st.sidebar.header("⚙️ Nastavenia výkonu")
pick_rate_default = st.sidebar.number_input("Norma (Joblines / hod na 1 človeka)", value=40, step=5)

# Stĺpce potrebné na výpočty
REQUIRED_COLUMNS = ['Vznik Line', 'Limit nanesení', 'Čas zvozu', 'JobLine', 'Geo Size produktu', 'RoutingType', 'Množstvo']

# --- FUNKCIA NA RÝCHLE NAČÍTANIE ---
@st.cache_data
def load_and_process_data(file):
    if file.name.endswith('.csv'):
        try:
            df = pd.read_csv(file, sep=';', usecols=lambda c: c in REQUIRED_COLUMNS)
            if len(df.columns) <= 1:
                file.seek(0)
                df = pd.read_csv(file, sep=',', usecols=lambda c: c in REQUIRED_COLUMNS)
        except:
            file.seek(0)
            df = pd.read_csv(file, sep=',', usecols=lambda c: c in REQUIRED_COLUMNS)
    else:
        try:
            df = pd.read_excel(file, engine='calamine', usecols=lambda c: c in REQUIRED_COLUMNS)
        except:
            df = pd.read_excel(file, engine='openpyxl', usecols=lambda c: c in REQUIRED_COLUMNS)
    
    # Prevody časov na hodiny
    df['Vznik Line dt'] = pd.to_datetime(df['Vznik Line'], dayfirst=True, errors='coerce')
    df['Hodina Vzniku'] = df['Vznik Line dt'].dt.hour

    # Extrakcia hodiny z Limit nanesení (zvládne dátum aj text "14:30")
    df['Limit str'] = df['Limit nanesení'].astype(str)
    df['Hodina Limitu'] = df['Limit str'].str.extract(r'(\d{1,2})').astype(float)
    
    return df

# --- UPLOAD SÚBORU ---
uploaded_file = st.file_uploader("Nahraj súbor (Excel / CSV)", type=["xlsx", "csv"])

if uploaded_file is not None:
    with st.spinner('Prepočítavam 120 000+ riadkov...'):
        df = load_and_process_data(uploaded_file)
        
    st.success(f"⚡ Načítané! Celkom {len(df):,} riadkov (Joblines).")

    st.sidebar.markdown("---")
    st.sidebar.header("🔍 Filtre")

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
        Vzniknute_v_hodine=('JobLine', 'count')
    ).reset_index()

    limits = filtered_df.groupby('Hodina Limitu').agg(
        Limit_v_hodine=('JobLine', 'count')
    ).reset_index()

    # Zlúčenie do súvislej časovej osi
    hourly = pd.merge(inflow, limits, left_on='Hodina Vzniku', right_on='Hodina Limitu', how='outer')
    hourly['Hodina'] = hourly['Hodina Vzniku'].fillna(hourly['Hodina Limitu'])
    hourly = hourly.dropna(subset=['Hodina'])
    hourly['Hodina'] = hourly['Hodina'].astype(int)
    hourly = hourly.sort_values('Hodina').fillna(0)

    # KUMULATÍVNE VÝPOČTY (Koľko už celkom vzniklo vs dokedy musí byť hotové)
    hourly['Kumulativne_Vzniknute'] = hourly['Vzniknute_v_hodine'].cumsum()
    hourly['Kumulativny_Limit'] = hourly['Limit_v_hodine'].cumsum()
    
    # Výpočet potrebných ľudí na danú hodinu podľa vzniku aj limitov
    hourly['Potrebni_Ludia_Vznik'] = (hourly['Vzniknute_v_hodine'] / pick_rate_default).round(1)
    hourly['Potrebni_Ludia_Limit'] = (hourly['Limit_v_hodine'] / pick_rate_default).round(1)

    # --- KPI METRIKY ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Celkový prítok (Joblines)", f"{len(filtered_df):,}")
    c2.metric("Najväčší vznik v hodine", f"{int(hourly['Vzniknute_v_hodine'].max()):,} ks")
    c3.metric("Najväčší limit v hodine", f"{int(hourly['Limit_v_hodine'].max()):,} ks")
    c4.metric("Odhad človekohodín", f"{round(len(filtered_df) / pick_rate_default, 1)} h")

    st.markdown("---")

    # --- GRAF 1: STAV PRÁCE (Vznik v hodine vs Kumulatívne k dispozícii) ---
    st.subheader("📊 1. Vznik práce a celková zásoba na pickovanie")
    
    fig1 = go.Figure()
    # Stĺpce: Nová práca prichádzajúca v hodine
    fig1.add_trace(go.Bar(
        x=hourly['Hodina'], 
        y=hourly['Vzniknute_v_hodine'],
        name='Nové Joblines v hodine',
        marker_color='#3366cc'
    ))
    # Čiara: Kumulatívny súčet (Koľko je už CELKOM k dispozícii)
    fig1.add_trace(go.Scatter(
        x=hourly['Hodina'], 
        y=hourly['Kumulativne_Vzniknute'],
        name='Kumulatívne vzniknuté (Zásoba)',
        mode='lines+markers',
        line=dict(color='#109618', width=3)
    ))
    fig1.update_layout(
        title="Prítok práce a nahromadená zásoba na pickovanie počas dňa",
        xaxis_title="Hodina",
        yaxis_title="Počet Joblines",
        xaxis=dict(dtick=1)
    )
    st.plotly_chart(fig1, use_container_width=True)

    # --- GRAF 2: DEADLINE (Limit nanesenia vs Vznik) ---
    st.subheader("🎯 2. Kedy najneskôr musí byť práca vypickovaná (Limit nanesenia)")
    
    fig2 = go.Figure()
    fig2.add_trace(go.Bar(
        x=hourly['Hodina'], 
        y=hourly['Limit_v_hodine'],
        name='Deadline v hodine (Limit nanesenia)',
        marker_color='#dc3912'
    ))
    fig2.add_trace(go.Scatter(
        x=hourly['Hodina'], 
        y=hourly['Kumulativny_Limit'],
        name='Kumulatívny deadline (Musí byť hotové)',
        mode='lines+markers',
        line=dict(color='#ff9900', width=3, dash='dash')
    ))
    fig2.update_layout(
        title="Termíny dokončenia (Limit nanesenia) po hodinách",
        xaxis_title="Hodina",
        yaxis_title="Počet Joblines",
        xaxis=dict(dtick=1)
    )
    st.plotly_chart(fig2, use_container_width=True)

    # --- GRAF 3: ODPORÚČANÝ POČET ĽUDÍ NA HODINY ---
    st.subheader("👥 3. Odporúčané pokrytie ľuďmi na hodinu")
    
    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(
        x=hourly['Hodina'], 
        y=hourly['Potrebni_Ludia_Vznik'],
        name='Ľudia podľa prítoku práce',
        mode='lines+markers',
        line=dict(color='#3366cc', width=2)
    ))
    fig3.add_trace(go.Scatter(
        x=hourly['Hodina'], 
        y=hourly['Potrebni_Ludia_Limit'],
        name='Ľudia podľa Limitu nanesenia (Deadline)',
        mode='lines+markers',
        line=dict(color='#dc3912', width=2)
    ))
    fig3.update_layout(
        title=f"Koľko ľudí treba na hodinu pri norme {pick_rate_default} lines/hod",
        xaxis_title="Hodina",
        yaxis_title="Počet pracovíkov (FTE)",
        xaxis=dict(dtick=1)
    )
    st.plotly_chart(fig3, use_container_width=True)

    # --- TABUĽKA ---
    with st.expander("📄 Zobraziť hodinovú tabuľku (Vznik, Zásoba, Limity, Ľudia)"):
        st.dataframe(
            hourly[['Hodina', 'Vzniknute_v_hodine', 'Kumulativne_Vzniknute', 'Limit_v_hodine', 'Kumulativny_Limit', 'Potrebni_Ludia_Limit']],
            use_container_width=True
        )

else:
    st.info("👋 Nahraj Excel alebo CSV súbor pre zobrazenie grafov.")

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

st.set_page_config(page_title="Kapacitné plánovanie skladu (06:00 - 06:00)", layout="wide")

st.title("📦 Kapacitné plánovanie skladu (Prevádzková zmena 06:00 - 06:00)")

# --- BOČNÝ PANEL ---
st.sidebar.header("⚙️ Nastavenia výkonu")
pick_rate_default = st.sidebar.number_input("Norma (Joblines / hod na 1 človeka)", value=40, step=5)

REQUIRED_COLUMNS = ['Vznik Line', 'Limit nanesení', 'Čas zvozu', 'JobLine', 'Geo Size produktu', 'RoutingType', 'Množstvo']

# --- NAČÍTANIE DÁT ---
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
    
    # Prevod časov na hodiny (0-23)
    df['Vznik Line dt'] = pd.to_datetime(df['Vznik Line'], dayfirst=True, errors='coerce')
    df['Hodina Vzniku'] = df['Vznik Line dt'].dt.hour

    df['Limit str'] = df['Limit nanesení'].astype(str)
    df['Hodina Limitu'] = df['Limit str'].str.extract(r'(\d{1,2})').astype(float)
    
    return df

uploaded_file = st.file_uploader("Nahraj súbor (Excel / CSV)", type=["xlsx", "csv"])

if uploaded_file is not None:
    with st.spinner('Prepočítavam dáta pre prevádzkovú zmenu 06:00 - 06:00...'):
        df = load_and_process_data(uploaded_file)
        
    st.success(f"⚡ Načítané! Celkom {len(df):,} riadkov (Joblines).")

    st.sidebar.markdown("---")
    st.sidebar.header("🔍 Filtre")

    geo_sizes = df['Geo Size produktu'].dropna().unique().tolist() if 'Geo Size produktu' in df.columns else []
    selected_geo = st.sidebar.multiselect("Geo Size produktu", options=geo_sizes, default=geo_sizes)
    
    routings = df['RoutingType'].dropna().unique().tolist() if 'RoutingType' in df.columns else []
    selected_routing = st.sidebar.multiselect("Routing Type", options=routings, default=routings)
    
    filtered_df = df.copy()
    if geo_sizes:
        filtered_df = filtered_df[filtered_df['Geo Size produktu'].isin(selected_geo)]
    if routings:
        filtered_df = filtered_df[filtered_df['RoutingType'].isin(selected_routing)]

    # --- AGREGÁCIA PODĽA HODÍN ---
    inflow = filtered_df.groupby('Hodina Vzniku').agg(Vzniknute_v_hodine=('JobLine', 'count')).reset_index()
    limits = filtered_df.groupby('Hodina Limitu').agg(Limit_v_hodine=('JobLine', 'count')).reset_index()

    # --- VYTVORENIE SÚVISLEJ ČASOVEJ OSI OD 06:00 DO 05:00 ---
    # Poradie hodín pre skladový deň: 6,7,8...,23,0,1,2,3,4,5
    shift_hours = [(6 + i) % 24 for i in range(24)]
    timeline_df = pd.DataFrame({'Hodina': shift_hours, 'Shift_Order': range(24)})

    # Spojenie s nameranými dátami
    hourly = pd.merge(timeline_df, inflow, left_on='Hodina', right_on='Hodina Vzniku', how='left')
    hourly = pd.merge(hourly, limits, left_on='Hodina', right_on='Hodina Limitu', how='left').fillna(0)

    # Zotriedenie presne podľa skladovej zmeny (06:00 -> 05:00)
    hourly = hourly.sort_values('Shift_Order')

    # KUMULATÍVNE VÝPOČTY (počítané v správnom poradí zmeny)
    hourly['Kumulativne_Vzniknute'] = hourly['Vzniknute_v_hodine'].cumsum()
    hourly['Kumulativny_Limit'] = hourly['Limit_v_hodine'].cumsum()
    
    # Výpočet potreby ľudí
    hourly['Potrebni_Ludia_Vznik'] = (hourly['Vzniknute_v_hodine'] / pick_rate_default).round(1)
    hourly['Potrebni_Ludia_Limit'] = (hourly['Limit_v_hodine'] / pick_rate_default).round(1)

    # Popisok pre os X (napr. "06:00", "07:00"... "00:00"... "05:00")
    hourly['Hodina_Label'] = hourly['Hodina'].astype(str).str.zfill(2) + ":00"

    # --- KPI METRIKY ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Celkový prítok (Joblines)", f"{len(filtered_df):,}")
    c2.metric("Najväčší vznik v hodine", f"{int(hourly['Vzniknute_v_hodine'].max()):,} ks")
    c3.metric("Najväčší limit v hodine", f"{int(hourly['Limit_v_hodine'].max()):,} ks")
    c4.metric("Odhad človekohodín", f"{round(len(filtered_df) / pick_rate_default, 1)} h")

    st.markdown("---")

    # --- GRAF 1: PRÍTOK VS ZÁSOBA (06:00 - 06:00) ---
    st.subheader("📊 1. Vznik práce a kumulatívna zásoba (Prevádzková zmena 06:00 - 06:00)")
    
    fig1 = go.Figure()
    fig1.add_trace(go.Bar(
        x=hourly['Hodina_Label'], 
        y=hourly['Vzniknute_v_hodine'],
        name='Nové Joblines v hodine',
        marker_color='#3366cc'
    ))
    fig1.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Kumulativne_Vzniknute'],
        name='Kumulatívne vzniknuté (Zásoba)',
        mode='lines+markers',
        line=dict(color='#109618', width=3)
    ))
    fig1.update_layout(
        title="Prítok práce a nahromadená zásoba počas prevádzkovej zmeny",
        xaxis_title="Prevádzková hodina",
        yaxis_title="Počet Joblines",
        xaxis=dict(type='category') # Zachová poradie od 06:00 do 05:00
    )
    st.plotly_chart(fig1, use_container_width=True)

    # --- GRAF 2: LIMIT NANESEENIA (DEADLINE) ---
    st.subheader("🎯 2. Požadované limity nanesenia (06:00 - 06:00)")
    
    fig2 = go.Figure()
    fig2.add_trace(go.Bar(
        x=hourly['Hodina_Label'], 
        y=hourly['Limit_v_hodine'],
        name='Limit nanesenia v hodine',
        marker_color='#dc3912'
    ))
    fig2.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Kumulativny_Limit'],
        name='Kumulatívny deadline (Musí byť hotové)',
        mode='lines+markers',
        line=dict(color='#ff9900', width=3, dash='dash')
    ))
    fig2.update_layout(
        title="Termíny dokončenia (Limit nanesenia) po hodinách zmeny",
        xaxis_title="Prevádzková hodina",
        yaxis_title="Počet Joblines",
        xaxis=dict(type='category')
    )
    st.plotly_chart(fig2, use_container_width=True)

    # --- GRAF 3: POTREBA ĽUDÍ ---
    st.subheader("👥 3. Odporúčané rozloženie ľudí na zmeny")
    
    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Potrebni_Ludia_Vznik'],
        name='Ľudia podľa prítoku práce',
        mode='lines+markers',
        line=dict(color='#3366cc', width=2)
    ))
    fig3.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Potrebni_Ludia_Limit'],
        name='Ľudia podľa Limitu nanesenia (Deadline)',
        mode='lines+markers',
        line=dict(color='#dc3912', width=2)
    ))
    fig3.update_layout(
        title=f"Počet ľudí na hodinu počas prevádzkovej zmeny (Norma = {pick_rate_default} lines/h)",
        xaxis_title="Prevádzková hodina",
        yaxis_title="Počet pracovníkov (FTE)",
        xaxis=dict(type='category')
    )
    st.plotly_chart(fig3, use_container_width=True)

    # --- TABUĽKA ---
    with st.expander("📄 Zobraziť hodinovú tabuľku (06:00 -> 05:00)"):
        st.dataframe(
            hourly[['Hodina_Label', 'Vzniknute_v_hodine', 'Kumulativne_Vzniknute', 'Limit_v_hodine', 'Kumulativny_Limit', 'Potrebni_Ludia_Limit']],
            use_container_width=True
        )

else:
    st.info("👋 Nahraj Excel alebo CSV súbor pre zobrazenie prevádzkovej zmeny.")

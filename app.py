import streamlit as st
import pandas as pd
import plotly.graph_objects as go

st.set_page_config(page_title="Kapacitné plánovanie skladu", layout="wide")

st.title("📦 Kapacitné plánovanie skladu (s Backlogom z minulých dní)")

# --- BOČNÝ PANEL ---
st.sidebar.header("⚙️ Nastavenia výkonu")
pick_rate_default = st.sidebar.number_input("Norma (Joblines / hod na 1 človeka)", value=40, step=5)

REQUIRED_COLUMNS = ['Vznik Line', 'Limit nanesení', 'Čas zvozu', 'JobLine', 'Geo Size produktu', 'RoutingType', 'Množstvo']

# --- NAČÍTANIE A KONVERZIA DÁT ---
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
    
    # Prevod stĺpcov na plnohodnotný dátum a čas (Datetime)
    df['Vznik_dt'] = pd.to_datetime(df['Vznik Line'], dayfirst=True, errors='coerce')
    df['Limit_dt'] = pd.to_datetime(df['Limit nanesení'], dayfirst=True, errors='coerce')
    
    return df

uploaded_file = st.file_uploader("Nahraj súbor (Excel / CSV)", type=["xlsx", "csv"])

if uploaded_file is not None:
    with st.spinner('Analýza dátumov a časov...'):
        raw_df = load_and_process_data(uploaded_file)
        
    st.sidebar.markdown("---")
    st.sidebar.header("📅 Výber Dňa a Filtre")

    # Extrakcia dostupných dátumov podľa Limitu nanesenia
    raw_df['Datum_Limitu'] = raw_df['Limit_dt'].dt.date
    available_dates = sorted(raw_df['Datum_Limitu'].dropna().unique())

    if not available_dates:
        st.error("⚠️ V stĺpci 'Limit nanesení' sa nenašli platné dátumy. Skontrolujte formát dátumu v Exceli.")
        st.stop()

    # Užívateľ si vyberie deň prevádzky
    selected_date = st.sidebar.selectbox("Vyber deň na plánovanie (podľa Limitu)", options=available_dates, index=0)

    # Definícia prevádzkovej zmeny: od 06:00 vybraného dňa do 05:59 nasledujúceho dňa
    shift_start = pd.Timestamp(selected_date).replace(hour=6, minute=0, second=0)
    shift_end = shift_start + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)

    # Odfiltrujeme len joblines, ktoré majú LIMIT NANESEENIA v tejto prevádzkovej zmene
    df = raw_df[(raw_df['Limit_dt'] >= shift_start) & (raw_df['Limit_dt'] <= shift_end)].copy()

    # Ostatné filtre
    geo_sizes = df['Geo Size produktu'].dropna().unique().tolist() if 'Geo Size produktu' in df.columns else []
    selected_geo = st.sidebar.multiselect("Geo Size produktu", options=geo_sizes, default=geo_sizes)
    
    routings = df['RoutingType'].dropna().unique().tolist() if 'RoutingType' in df.columns else []
    selected_routing = st.sidebar.multiselect("Routing Type", options=routings, default=routings)
    
    filtered_df = df.copy()
    if geo_sizes:
        filtered_df = filtered_df[filtered_df['Geo Size produktu'].isin(selected_geo)]
    if routings:
        filtered_df = filtered_df[filtered_df['RoutingType'].isin(selected_routing)]

    # --- LOGIKA PRE VZNIK PRÁCE (BACKLOG VS PRÍTOK) ---
    def assign_effective_hour(row):
        vznik = row['Vznik_dt']
        if pd.isnull(vznik) or vznik < shift_start:
            # Ak vznikla pred začiatkom zmeny (včera/predtým), je dostupná hneď o 06:00
            return 6
        elif vznik > shift_end:
            # Ak vznikla až po zmene (nemalo by stať), dáme do poslednej hodiny
            return 5
        else:
            # Reálna hodina vzniknutia počas zmeny
            return vznik.hour

    filtered_df['Efektivna_Hodina_Vzniku'] = filtered_df.apply(assign_effective_hour, axis=1)
    filtered_df['Hodina_Limitu'] = filtered_df['Limit_dt'].dt.hour

    # --- AGREGÁCIA PODIELOV PO HODINÁCH ---
    inflow = filtered_df.groupby('Efektivna_Hodina_Vzniku').agg(Vzniknute_v_hodine=('JobLine', 'count')).reset_index()
    limits = filtered_df.groupby('Hodina_Limitu').agg(Limit_v_hodine=('JobLine', 'count')).reset_index()

    # Časová os prevádzkovej zmeny: 6,7..23, 0..5
    shift_hours = [(6 + i) % 24 for i in range(24)]
    timeline_df = pd.DataFrame({'Hodina': shift_hours, 'Shift_Order': range(24)})

    hourly = pd.merge(timeline_df, inflow, left_on='Hodina', right_on='Efektivna_Hodina_Vzniku', how='left')
    hourly = pd.merge(hourly, limits, left_on='Hodina', right_on='Hodina_Limitu', how='left').fillna(0)
    hourly = hourly.sort_values('Shift_Order')

    # Kumulatívne súčty
    hourly['Kumulativne_Vzniknute'] = hourly['Vzniknute_v_hodine'].cumsum()
    hourly['Kumulativny_Limit'] = hourly['Limit_v_hodine'].cumsum()
    
    # Výpočet potreby ľudí
    hourly['Potrebni_Ludia_Limit'] = (hourly['Limit_v_hodine'] / pick_rate_default).round(1)

    hourly['Hodina_Label'] = hourly['Hodina'].astype(str).str.zfill(2) + ":00"

    # Počet starých joblines (Backlog z minulých dní)
    backlog_count = len(filtered_df[filtered_df['Vznik_dt'] < shift_start])

    # --- KPI METRIKY ---
    st.success(f"📅 Plán pre deň: **{selected_date.strftime('%d.%m.%Y')}** (Zmena: {shift_start.strftime('%d.%m. %H:%M')} – {shift_end.strftime('%d.%m. %H:%M')})")
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Celkom Joblines pre tento deň", f"{len(filtered_df):,}")
    c2.metric("📦 Z toho Backlog (vzniklo včera/skôr)", f"{backlog_count:,}", help="Tieto joblines vznikli pred 06:00 a sú pripravené na pickovanie hneď na začiatku zmeny.")
    c3.metric("Najväčší prítok v hodine", f"{int(hourly['Vzniknute_v_hodine'].max()):,} ks")
    c4.metric("Odhad človekohodín", f"{round(len(filtered_df) / pick_rate_default, 1)} h")

    st.markdown("---")

    # --- GRAF 1: VZNIK VS KUMULATÍVNA ZÁSOBA ---
    st.subheader("📊 1. Zásoba práce na pickovanie (vrátane Backlogu)")
    
    fig1 = go.Figure()
    fig1.add_trace(go.Bar(
        x=hourly['Hodina_Label'], 
        y=hourly['Vzniknute_v_hodine'],
        name='Prítok v hodine (o 06:00 aj Backlog)',
        marker_color='#3366cc'
    ))
    fig1.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Kumulativne_Vzniknute'],
        name='Kumulatívna zásoba na pickovanie',
        mode='lines+markers',
        line=dict(color='#109618', width=3)
    ))
    fig1.update_layout(
        title="Dostupná práca po hodinách (Všimnite si vysokú zásobu o 06:00 vďaka backlogu)",
        xaxis_title="Prevádzková hodina",
        yaxis_title="Počet Joblines",
        xaxis=dict(type='category')
    )
    st.plotly_chart(fig1, use_container_width=True)

    # --- GRAF 2: LIMIT NANESEENIA ---
    st.subheader("🎯 2. Požadované limity nanesenia (Deadlines)")
    
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
        name='Kumulatívny deadline',
        mode='lines+markers',
        line=dict(color='#ff9900', width=3, dash='dash')
    ))
    fig2.update_layout(
        title="Dokedy najneskôr musia byť joblines vypickované",
        xaxis_title="Prevádzková hodina",
        yaxis_title="Počet Joblines",
        xaxis=dict(type='category')
    )
    st.plotly_chart(fig2, use_container_width=True)

    # --- GRAF 3: POTREBA ĽUDÍ ---
    st.subheader("👥 3. Odporúčané pokrytie ľuďmi na zmeny")
    
    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Potrebni_Ludia_Limit'],
        name='Potrební ľudia podľa Limitu nanesenia',
        mode='lines+markers',
        line=dict(color='#dc3912', width=3)
    ))
    fig3.update_layout(
        title=f"Počet ľudí potrebných na hodinu pre splnenie limitov (Norma = {pick_rate_default} lines/h)",
        xaxis_title="Prevádzková hodina",
        yaxis_title="Počet pracovníkov (FTE)",
        xaxis=dict(type='category')
    )
    st.plotly_chart(fig3, use_container_width=True)

    # --- TABUĽKA ---
    with st.expander("📄 Zobraziť hodinovú tabuľku dát"):
        st.dataframe(
            hourly[['Hodina_Label', 'Vzniknute_v_hodine', 'Kumulativne_Vzniknute', 'Limit_v_hodine', 'Kumulativny_Limit', 'Potrebni_Ludia_Limit']],
            use_container_width=True
        )

else:
    st.info("👋 Nahraj Excel alebo CSV súbor pre zobrazenie prevádzkovej zmeny.")

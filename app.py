import streamlit as st
import pandas as pd
import plotly.graph_objects as go

st.set_page_config(page_title="Kapacitné plánovanie & WIP Balanc", layout="wide")

st.title("📦 Vyrovnávanie kapacít a Zostatok práce na sklade (WIP)")

# --- BOČNÝ PANEL ---
st.sidebar.header("⚙️ Nastavenia výkonu")
pick_rate_default = st.sidebar.number_input("Norma (Joblines / hod na 1 človeka)", value=40, step=5)

st.sidebar.markdown("---")
st.sidebar.header("🎛️ Plánovanie zmeny")
simulated_workers = st.sidebar.slider("Konštantný počet ľudí na zmene", min_value=1, max_value=100, value=15)

REQUIRED_COLUMNS = ['Vznik Line', 'Limit nanesení', 'Čas zvozu', 'JobLine', 'Geo Size produktu', 'RoutingType', 'Množstvo']

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
    
    df['Vznik_dt'] = pd.to_datetime(df['Vznik Line'], dayfirst=True, errors='coerce')
    df['Limit_dt'] = pd.to_datetime(df['Limit nanesení'], dayfirst=True, errors='coerce')
    return df

uploaded_file = st.file_uploader("Nahraj súbor (Excel / CSV)", type=["xlsx", "csv"])

if uploaded_file is not None:
    raw_df = load_and_process_data(uploaded_file)
    
    st.sidebar.markdown("---")
    st.sidebar.header("📅 Výber Dňa a Filtre")

    raw_df['Datum_Limitu'] = raw_df['Limit_dt'].dt.date
    available_dates = sorted(raw_df['Datum_Limitu'].dropna().unique())

    if not available_dates:
        st.error("⚠️ V stĺpci 'Limit nanesení' sa nenašli platné dátumy.")
        st.stop()

    selected_date = st.sidebar.selectbox("Vyber deň na plánovanie", options=available_dates, index=0)

    shift_start = pd.Timestamp(selected_date).replace(hour=6, minute=0, second=0)
    shift_end = shift_start + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)

    df = raw_df[(raw_df['Limit_dt'] >= shift_start) & (raw_df['Limit_dt'] <= shift_end)].copy()

    geo_sizes = df['Geo Size produktu'].dropna().unique().tolist() if 'Geo Size produktu' in df.columns else []
    selected_geo = st.sidebar.multiselect("Geo Size produktu", options=geo_sizes, default=geo_sizes)
    
    routings = df['RoutingType'].dropna().unique().tolist() if 'RoutingType' in df.columns else []
    selected_routing = st.sidebar.multiselect("Routing Type", options=routings, default=routings)
    
    filtered_df = df.copy()
    if geo_sizes:
        filtered_df = filtered_df[filtered_df['Geo Size produktu'].isin(selected_geo)]
    if routings:
        filtered_df = filtered_df[filtered_df['RoutingType'].isin(selected_routing)]

    def assign_effective_hour(row):
        vznik = row['Vznik_dt']
        if pd.isnull(vznik) or vznik < shift_start:
            return 6
        elif vznik > shift_end:
            return 5
        else:
            return int(vznik.hour)

    filtered_df['Efektivna_Hodina_Vzniku'] = filtered_df.apply(assign_effective_hour, axis=1)
    filtered_df['Hodina_Limitu'] = filtered_df['Limit_dt'].dt.hour.fillna(0).astype(int)

    inflow = filtered_df.groupby('Efektivna_Hodina_Vzniku').agg(Vzniknute_v_hodine=('JobLine', 'count')).reset_index()
    limits = filtered_df.groupby('Hodina_Limitu').agg(Limit_v_hodine=('JobLine', 'count')).reset_index()

    shift_hours = [(6 + i) % 24 for i in range(24)]
    timeline_df = pd.DataFrame({'Hodina': shift_hours, 'Shift_Order': range(24)})

    hourly = pd.merge(timeline_df, inflow, left_on='Hodina', right_on='Efektivna_Hodina_Vzniku', how='left')
    hourly = pd.merge(hourly, limits, left_on='Hodina', right_on='Hodina_Limitu', how='left').fillna(0)
    hourly = hourly.sort_values('Shift_Order')

    hourly['Kumulativne_Vzniknute'] = hourly['Vzniknute_v_hodine'].cumsum()
    hourly['Kumulativny_Limit'] = hourly['Limit_v_hodine'].cumsum()
    hourly['Hodina_Label'] = hourly['Hodina'].astype(str).str.zfill(2) + ":00"

    # --- SIMULÁCIA BALANCU A ZOSTATKU PRÁCE (WIP) ---
    hourly_capacity = simulated_workers * pick_rate_default
    
    current_wip = 0
    simulated_picked_hourly = []
    wip_at_end_of_hour = []
    cumulative_picked = []
    total_picked_so_far = 0

    for idx, row in hourly.iterrows():
        new_inflow = row['Vzniknute_v_hodine']
        total_available = current_wip + new_inflow
        
        # Vypickujeme toľko, koľko je kapacita ľudí, ale max. toľko, koľko je dostupné
        actually_picked = min(total_available, hourly_capacity)
        
        # Zostatok práce na sklade po tejto hodine
        current_wip = total_available - actually_picked
        total_picked_so_far += actually_picked
        
        simulated_picked_hourly.append(actually_picked)
        wip_at_end_of_hour.append(current_wip)
        cumulative_picked.append(total_picked_so_far)

    hourly['Vypickovane_v_hodine'] = simulated_picked_hourly
    hourly['Zostatok_Prace_WIP'] = wip_at_end_of_hour
    hourly['Kumulativne_Vypickovane'] = cumulative_picked
    
    # Kontrola meškania voči limitom nanesenia
    hourly['Meskanie'] = hourly['Kumulativne_Vypickovane'] < hourly['Kumulativny_Limit']
    has_delay = hourly['Meskanie'].any()

    # --- KPI METRIKY ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Celkom Joblines na deň", f"{len(filtered_df):,}")
    c2.metric("Kapacita tímu / hodina", f"{hourly_capacity:,} lines")
    c3.metric("Zostatok na konci zmeny (o 05:00)", f"{int(hourly.iloc[-1]['Zostatok_Prace_WIP']):,} lines")
    
    if has_delay:
        c4.metric("Stav plnenia limitov", "⚠️ MEŠKANIE DÔLEŽITÝCH LIMITOV!", delta_color="inverse")
    else:
        c4.metric("Stav plnenia limitov", "✅ VŠETKY LIMITI SPLNENÉ", delta_color="normal")

    st.markdown("---")

    # --- GRAF 1: HODINOVÁ BILANCIA (PRÍTOK VS VYPICKOVANÉ VS ZOSTATOK) ---
    st.subheader("📊 1. Hodinový tok: Vznik práce vs. Vypickované vs. Zostatok (WIP)")
    
    fig_flow = go.Figure()
    # Nové vzniknuté
    fig_flow.add_trace(go.Bar(
        x=hourly['Hodina_Label'], 
        y=hourly['Vzniknute_v_hodine'],
        name='1. Nové Vzniknuté joblines',
        marker_color='#2ca02c',
        opacity=0.7
    ))
    # Vypickované ľuďmi
    fig_flow.add_trace(go.Bar(
        x=hourly['Hodina_Label'], 
        y=hourly['Vypickovane_v_hodine'],
        name=f'2. Vypickované ({simulated_workers} ľudí)',
        marker_color='#9467bd'
    ))
    # Čiara zostatku (WIP)
    fig_flow.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Zostatok_Prace_WIP'],
        name='3. ZOSTATOK PRÁCE NA SKLADE (WIP)',
        mode='lines+markers',
        line=dict(color='#ff7f0e', width=3)
    ))
    fig_flow.update_layout(
        barmode='group',
        title="Priebeh spracovania práce a aktuálny zostatok čakania na pickovanie",
        xaxis_title="Prevádzková hodina (06:00 -> 05:00)",
        yaxis_title="Počet Joblines",
        xaxis=dict(type='category')
    )
    st.plotly_chart(fig_flow, use_container_width=True)

    # --- GRAF 2: KUMULATÍVNY SÚČET A KONTROLA LIMITOV ---
    st.subheader("📈 2. Vyrovnaná kumulatívna krivka vs. Limity nanesenia")

    fig_cum = go.Figure()

    # Max Vzniknuté
    fig_cum.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Kumulativne_Vzniknute'],
        name='🟢 Vzniknuté celkom (Zásoba)',
        mode='lines',
        line=dict(color='#2ca02c', width=2)
    ))

    # Reálne vypickované vyrovnanou kapacitou
    fig_cum.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Kumulativne_Vypickovane'],
        name=f'🟣 Kumulatívne Vypickované ({simulated_workers} ľudí)',
        mode='lines+markers',
        line=dict(color='#9467bd', width=4)
    ))

    # Min Limit
    fig_cum.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Kumulativny_Limit'],
        name='🔴 MINIMUM: Limit nanesenia',
        mode='lines+markers',
        line=dict(color='#d62728', width=3, dash='dash')
    ))

    fig_cum.update_layout(
        title="Vyrovnaný priebeh pickovania: Krivka Vypickované musí byť VŽDY NAD červenou čiarou",
        xaxis_title="Prevádzková hodina (06:00 -> 05:00)",
        yaxis_title="Kumulatívny počet Joblines",
        xaxis=dict(type='category'),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
    )

    st.plotly_chart(fig_cum, use_container_width=True)

    # Upozornenie
    if has_delay:
        delayed_hours = hourly[hourly['Meskanie']]['Hodina_Label'].tolist()
        st.error(f"🚨 Pri počte {simulated_workers} ľudí vznikne meškanie voči limitom v týchto hodinách: {', '.join(delayed_hours)}. Pridajte ľudí!")
    else:
        st.success(f"🎉 Skvelé! S kapacitou {simulated_workers} ľudí stíhate všetky limity nanesenia a práca je vyrovnaná.")

    # --- DETAILNÁ TABUĽKA ---
    with st.expander("📄 Zobraziť hodinovú bilanciu (Prítok, Výkon, Zostatok, Deadlines)"):
        st.dataframe(
            hourly[['Hodina_Label', 'Vzniknute_v_hodine', 'Vypickovane_v_hodine', 'Zostatok_Prace_WIP', 'Limit_v_hodine', 'Kumulativny_Limit', 'Kumulativne_Vypickovane']],
            use_container_width=True
        )

else:
    st.info("👋 Nahraj súbor pre zobrazenie bilancie práce.")

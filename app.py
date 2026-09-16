import streamlit as st
import pandas as pd
import plotly.graph_objects as go

st.set_page_config(page_title="Kapacitné plánovanie & Simulátor", layout="wide")

st.title("📦 Vyrovnávanie kapacít a Simulátor rozloženia práce")

# --- BOČNÝ PANEL ---
st.sidebar.header("⚙️ Nastavenia výkonu")
pick_rate_default = st.sidebar.number_input("Norma (Joblines / hod na 1 človeka)", value=40, step=5)

st.sidebar.markdown("---")
st.sidebar.header("🎛️ Simulátor smien (Počet ľudí)")
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
    selected_routing = st.sidebar.multiselect("Routing Type", options=routings, default=selected_routing if 'selected_routing' in locals() else routings)
    
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
            return vznik.hour

    filtered_df['Efektivna_Hodina_Vzniku'] = filtered_df.apply(assign_effective_hour, axis=1)
    filtered_df['Hodina_Limitu'] = filtered_df['Limit_dt'].dt.hour

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

    # --- SIMULÁCIA VYROVNANÉHO VÝKONU ---
    # Výkon tímu za 1 hodinu
    hourly_capacity = simulated_workers * pick_rate_default

    simulated_picked = []
    current_total_picked = 0

    for idx, row in hourly.iterrows():
        max_available = row['Kumulativne_Vzniknute']
        # Skúsime odpickovať hodinovú kapacitu, ale maximálne toľko, koľko už celkom vzniklo
        current_total_picked = min(max_available, current_total_picked + hourly_capacity)
        simulated_picked.append(current_total_picked)

    hourly['Simulovane_Vypickovane'] = simulated_picked

    # Kontrola meškania (Ak Simulované vypickované < Kumulatívny Limit)
    hourly['Meskanie'] = hourly['Simulovane_Vypickovane'] < hourly['Kumulativny_Limit']
    has_delay = hourly['Meskanie'].any()

    # --- KPI METRIKY ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Celkom Joblines", f"{len(filtered_df):,}")
    c2.metric("Nastavený počet ľudí", f"{simulated_workers} ľudí")
    c3.metric("Kapacita tímu za hodinu", f"{hourly_capacity:,} lines/h")
    
    if has_delay:
        c4.metric("Stav simulácie", "⚠️ VZNIKNE MEŠKANIE!", delta_color="inverse")
    else:
        c4.metric("Stav simulácie", "✅ VŠETKO SA STÍHA", delta_color="normal")

    st.markdown("---")

    # --- MAIN GRAF: KUMULATÍVNY FLOW DIAGRAM (CFD) ---
    st.subheader("📈 Kumulatívny diagram toku (Okno príležitosti vs. Simulácia)")
    st.caption("Modrá zóna medzi zelenou a červenou čiarou ukazuje okno, kedy sa dá práca roztiahnuť. Fialová čiara je tvoj nastavený plán ľudí.")

    fig = go.Figure()

    # Zelená čiara - Max dostupné na pickovanie
    fig.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Kumulativne_Vzniknute'],
        name='1. MAX MOŽNÉ (Vzniknuté joblines)',
        mode='lines',
        line=dict(color='#2ca02c', width=3),
        fill=None
    ))

    # Červená čiara - Min čo musí byť vypickované
    fig.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Kumulativny_Limit'],
        name='2. MIN POŽADOVANÉ (Limit nanesenia)',
        mode='lines',
        line=dict(color='#d62728', width=3, dash='dash'),
        fill='tonexty', # Vytvorí farebnú zónu medzi zelenou a červenou
        fillcolor='rgba(31, 119, 180, 0.15)'
    ))

    # Fialová čiara - Simulovaný priebeh podľa nastaveného počtu ľudí
    fig.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Simulovane_Vypickovane'],
        name=f'3. SIMULÁCIA ({simulated_workers} ľudí x {pick_rate_default} lines/h)',
        mode='lines+markers',
        line=dict(color='#9467bd', width=4)
    ))

    fig.update_layout(
        title="Simulácia priebehu pickovania vs. Limity nanesenia",
        xaxis_title="Prevádzková hodina (06:00 -> 05:00)",
        yaxis_title="Kumulatívny počet Joblines",
        xaxis=dict(type='category'),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
    )

    st.plotly_chart(fig, use_container_width=True)

    # Upozornenie ak vznikne meškanie
    if has_delay:
        delayed_hours = hourly[hourly['Meskanie']]['Hodina_Label'].tolist()
        st.error(f"🚨 Pri počte {simulated_workers} ľudí vznikne meškanie voči limitom nanesenia v týchto hodinách: {', '.join(delayed_hours)}. Zvýšte počet ľudí v bočnom paneli!")
    else:
        st.success(f"🎉 Skvelé! S {simulated_workers} ľuďmi stíhate všetky limity nanesenia. Práca je plynule roztiahnutá počas celého dňa.")

    # --- DETIALNÁ TABUĽKA ---
    with st.expander("📄 Zobraziť detailný hodinový priebeh simulácie"):
        st.dataframe(
            hourly[['Hodina_Label', 'Kumulativne_Vzniknute', 'Kumulativny_Limit', 'Simulovane_Vypickovane', 'Meskanie']],
            use_container_width=True
        )

else:
    st.info("👋 Nahraj súbor pre spustenie simulácie.")

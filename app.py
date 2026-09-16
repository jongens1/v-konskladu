import streamlit as st
import pandas as pd
import plotly.graph_objects as go

st.set_page_config(page_title="Operatívny stav práce v sklade", layout="wide")

st.title("📦 Prehľad voľnej práce a zostatkov na pickovanie")

# --- BOČNÝ PANEL ---
st.sidebar.header("🎛️ Testovací výkon skladu")
test_output_rate = st.sidebar.number_input("Nastav požadovaný výkon skladu za hodinu (Joblines / hod)", value=1000, step=50)

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
    hourly = hourly.sort_values('Shift_Order').reset_index(drop=True)

    hourly['Kumulativne_Vzniknute'] = hourly['Vzniknute_v_hodine'].cumsum()
    hourly['Kumulativny_Limit'] = hourly['Limit_v_hodine'].cumsum()
    hourly['Hodina_Label'] = hourly['Hodina'].astype(str).str.zfill(2) + ":00"

    # --- SIMULÁCIA PRÁCE S PREHĽADOM VOĽNÝCH JOBLINES ---
    cum_picked = 0
    volne_na_pick_list = []
    cum_picked_list = []
    vypickovane_v_hodine_list = []

    for idx, row in hourly.iterrows():
        total_created_so_far = row['Kumulativne_Vzniknute']
        
        # Kolko bolo voľné na začiatku hodiny (pred pickovaním)
        available_before_pick = total_created_so_far - cum_picked
        
        # Kolko reálne v tejto hodine odpickujeme
        picked_this_hour = min(available_before_pick, test_output_rate)
        cum_picked += picked_this_hour
        
        # Zostatok voľných joblines NA KONCI hodiny
        volne_na_pick_end = total_created_so_far - cum_picked
        
        vypickovane_v_hodine_list.append(picked_this_hour)
        cum_picked_list.append(cum_picked)
        volne_na_pick_list.append(volne_na_pick_end)

    hourly['Vypickovane_v_hodine'] = vypickovane_v_hodine_list
    hourly['Kumulativne_Vypickovane'] = cum_picked_list
    hourly['Volne_na_Pickovanie'] = volne_na_pick_list

    # Kontrola meškania voči limitom
    hourly['Meskanie'] = hourly['Kumulativne_Vypickovane'] < hourly['Kumulativny_Limit']
    has_delay = hourly['Meskanie'].any()

    # --- KPI METRIKY ---
    st.info(f"📅 Plán pre deň: **{selected_date.strftime('%d.%m.%Y')}**")
    
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Celkom Joblines na deň", f"{len(filtered_df):,}")
    c2.metric("Napadnuté pred 06:00 (Backlog)", f"{int(hourly.iloc[0]['Vzniknute_v_hodine']):,} lines")
    c3.metric("Nastavený výkon skladu", f"{test_output_rate:,} lines / hod")
    
    if has_delay:
        c4.metric("Stav plnenia limitov", "⚠️ VZNIKNE MEŠKANIE!", delta_color="inverse")
    else:
        c4.metric("Stav plnenia limitov", "✅ LIMITI SPLNENÉ", delta_color="normal")

    st.markdown("---")

    # --- GRAF 1: AKTUÁLNA ZÁSOBA VOĽNÝCH JOBLINES NA SKLADE ---
    st.subheader("📦 1. Aktuálna zásoba voľných joblines na sklade (Čakajú na vypickovanie)")
    st.caption("Oranžové stĺpce ukazujú, koľko joblines fyzicky leží na sklade pripravených na pickovanie v každej hodine.")

    fig_stock = go.Figure()
    fig_stock.add_trace(go.Bar(
        x=hourly['Hodina_Label'], 
        y=hourly['Volne_na_Pickovanie'],
        name='Voľné joblines na pickovanie (Zásoba)',
        marker_color='#ff7f0e'
    ))
    fig_stock.update_layout(
        title="Koľko joblines je v danej hodine k dispozícii v regáloch (po odpočítaní už vypickovaných)",
        xaxis_title="Prevádzková hodina (06:00 -> 05:00)",
        yaxis_title="Počet voľných Joblines",
        xaxis=dict(type='category')
    )
    st.plotly_chart(fig_stock, use_container_width=True)

    # --- GRAF 2: VERIFIKÁCIA S LIMITMI ---
    st.subheader("📈 2. Porovnanie: Celkovo vzniklo vs. Vypickované vs. Limity")

    fig_cum = go.Figure()

    # Vzniklo celkom (Strop)
    fig_cum.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Kumulativne_Vzniknute'],
        name='🟢 Celkovo Vzniklo (Strop)',
        mode='lines',
        line=dict(color='#2ca02c', width=3)
    ))

    # Odpracované (Vypickované)
    fig_cum.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Kumulativne_Vypickovane'],
        name=f'🟣 Odpracované / Vypickované ({test_output_rate} lines/h)',
        mode='lines+markers',
        line=dict(color='#9467bd', width=4)
    ))

    # Limit (Dôležitý prah)
    fig_cum.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Kumulativny_Limit'],
        name='🔴 Limity nanesenia (Minimum)',
        mode='lines+markers',
        line=dict(color='#d62728', width=3, dash='dash')
    ))

    fig_cum.update_layout(
        title="Fialová čiara odpracovanej práce musí prechádzať medzi Zelenou (Vznik) a Červenou (Limit)",
        xaxis_title="Prevádzková hodina (06:00 -> 05:00)",
        yaxis_title="Kumulatívny počet Joblines",
        xaxis=dict(type='category'),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
    )

    st.plotly_chart(fig_cum, use_container_width=True)

    # --- TABUĽKA S PRESNÝMI ČÍSLAMI ---
    st.subheader("📄 Hodinový prehľad dát")
    st.dataframe(
        hourly[['Hodina_Label', 'Vzniknute_v_hodine', 'Kumulativne_Vzniknute', 'Kumulativne_Vypickovane', 'Volne_na_Pickovanie', 'Limit_v_hodine', 'Kumulativny_Limit']],
        use_container_width=True
    )

else:
    st.info("👋 Nahraj súbor pre zobrazenie stavu voľnej práce.")

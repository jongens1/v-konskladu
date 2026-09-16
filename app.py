import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import numpy as np

st.set_page_config(page_title="Analýza požadovaného výkonu skladu", layout="wide")

st.title("🎯 Analýza požadovaného výkonu skladu (Joblines / hod)")
st.caption("Cieľ: Zistiť, aký hodinový výkon musí sklad dosahovať na dosiahnutie 100% plnenia limitov nanesenia.")

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

uploaded_file = st.file_uploader("Nahraj súbor z pondelka (Excel / CSV)", type=["xlsx", "csv"])

if uploaded_file is not None:
    raw_df = load_and_process_data(uploaded_file)
    
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

    # Priradenie hodín v rámci prevádzkovej zmeny 06:00 -> 05:00
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

    # --- MATEMATICKÝ VÝPOČET MINIMÁLNEHO VYROVNANÉHO VÝKONU (SMOOTHED RATE) ---
    # Koľko Joblines/hodinu musíme MINIMÁLNE spraviť od prvej hodine zmeny, aby sme stíhali limity?
    hourly['Potrebny_Staly_Vykon'] = hourly['Kumulativny_Limit'] / (hourly.index + 1)
    min_required_constant_rate = int(np.ceil(hourly['Potrebny_Staly_Vykon'].max()))

    # Krivka vyrovnaného výkonu (Target Cumulative Line)
    target_cumulative = []
    target_hourly_output = []
    prev_cum = 0

    for idx, row in hourly.iterrows():
        # Maximálne môžeme spraviť toľko, koľko už vzniklo
        max_possible = row['Kumulativne_Vzniknute']
        ideal_cum = min(max_possible, min_required_constant_rate * (idx + 1))
        
        target_cumulative.append(ideal_cum)
        target_hourly_output.append(ideal_cum - prev_cum)
        prev_cum = ideal_cum

    hourly['Cielovy_Kumulativny_Vykon'] = target_cumulative
    hourly['Cielovy_Hodinovy_Vykon'] = target_hourly_output

    # --- KPI METRIKY ---
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Celkový objem zákaziek", f"{len(filtered_df):,} lines")
    c2.metric("Minimálny vyrovnaný výkon skladu", f"{min_required_constant_rate:,} lines / hod", help="Ak sklad udrží tento stály hodinový výkon od 06:00, stihne všetky limity nanesenia bez meškania.")
    c3.metric("Najväčšia špička limitov (Reaktívna)", f"{int(hourly['Limit_v_hodine'].max()):,} lines / hod")
    c4.metric("Úspora špičkového výkonu", f"{int(hourly['Limit_v_hodine'].max() - min_required_constant_rate):,} lines / hod")

    st.markdown("---")

    # --- GRAF 1: POŽADOVANÝ HODINOVÝ VÝKON SKLADU (Lines / hod) ---
    st.subheader("📊 1. Požadovaný hodinový výkon skladu (Porovnanie stratégií)")
    st.caption("Červené stĺpce ukazujú, aký obrovský reaktívny výkon by ste potrebovali v špičkách. Modrá čiara ukazuje VYROVNANÝ cieľový výkon skladu.")

    fig_rate = go.Figure()

    # Reaktívny výkon (JIT)
    fig_rate.add_trace(go.Bar(
        x=hourly['Hodina_Label'], 
        y=hourly['Limit_v_hodine'],
        name='Reaktívny výkon (Spracovanie až v hodine limitu)',
        marker_color='#ff7f0e',
        opacity=0.6
    ))

    # Vyrovnaný výkon
    fig_rate.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Cielovy_Hodinovy_Vykon'],
        name=f'🎯 Vyrovnaný cieľový výkon ({min_required_constant_rate} lines/h)',
        mode='lines+markers',
        line=dict(color='#1f77b4', width=4)
    ))

    fig_rate.update_layout(
        title="Hodinový cieľový výkon skladu vs. Reaktívne špičky",
        xaxis_title="Prevádzková hodina (06:00 -> 05:00)",
        yaxis_title="Požadovaný výkon (Joblines / hodina)",
        xaxis=dict(type='category'),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
    )

    st.plotly_chart(fig_rate, use_container_width=True)

    # --- GRAF 2: KUMULATÍVNA KAPACITNÁ KRIVKA ---
    st.subheader("📈 2. Kumulatívny priebeh: Zásoba vs. Limity vs. Cieľová čiara")

    fig_cum = go.Figure()

    # Dostupné
    fig_cum.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Kumulativne_Vzniknute'],
        name='🟢 Vzniknuté (Maximálne dostupné na pick)',
        mode='lines',
        line=dict(color='#2ca02c', width=3)
    ))

    # Limity
    fig_cum.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Kumulativny_Limit'],
        name='🔴 Limity nanesenia (Prah meškania)',
        mode='lines+markers',
        line=dict(color='#d62728', width=3, dash='dash')
    ))

    # Vyrovnaná cieľová trajektória
    fig_cum.add_trace(go.Scatter(
        x=hourly['Hodina_Label'], 
        y=hourly['Cielovy_Kumulativny_Vykon'],
        name='🟦 Vyrovnaná cieľová trajektória skladu',
        mode='lines+markers',
        line=dict(color='#1f77b4', width=4)
    ))

    fig_cum.update_layout(
        title="Kumulatívna analýza: Modrá čiara spája vysokú zásobu vzniknutých joblines s limitmi nanesenia",
        xaxis_title="Prevádzková hodina (06:00 -> 05:00)",
        yaxis_title="Kumulatívny počet Joblines",
        xaxis=dict(type='category'),
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01)
    )

    st.plotly_chart(fig_cum, use_container_width=True)

    # --- VOLITEĽNÁ ANALÝZA ĽUDSKÝCH ZDROJOV NA ZÁVEREČNÝ PREPOČET ---
    st.markdown("---")
    st.subheader("🧮 Prepočet cieľového výkonu na počet ľudí (Pre analýzu)")
    
    col_anal1, col_anal2 = st.columns(2)
    with col_anal1:
        assumed_pick_rate = st.number_input("Očakávaná priemerná norma/výkon 1 človeka (Joblines / hodina)", value=40, step=5)
    
    required_ftes = round(min_required_constant_rate / assumed_pick_rate, 1)
    
    with col_anal2:
        st.info(f"💡 Na dosiahnutie cieľového vyrovnaného výkonu **{min_required_constant_rate} lines/h** pri norme **{assumed_pick_rate} lines/h/človek** budete potrebovať konštantne: **{required_ftes} ľudí** na zmene.")

    # --- TABUĽKA ---
    with st.expander("📄 Zobraziť detailnú tabuľku požadovaných výkonov"):
        st.dataframe(
            hourly[['Hodina_Label', 'Vzniknute_v_hodine', 'Kumulativne_Vzniknute', 'Limit_v_hodine', 'Kumulativny_Limit', 'Cielovy_Hodinovy_Vykon', 'Cielovy_Kumulativny_Vykon']],
            use_container_width=True
        )

else:
    st.info("👋 Nahraj súbor z pondelka pre výpočet požadovaného výkonu skladu.")

# Test Sample Data

Five CSV files designed to exercise all three agents across different domains and query types.

---

## Files

### 1. `sales_transactions.csv` (40 rows)
Retail sales orders across Jan–Apr 2025.

**Columns:** `order_id`, `order_date`, `product_name`, `category`, `region`, `sales_rep`, `quantity`, `unit_price`, `discount_pct`, `total_revenue`, `customer_id`

**Good queries to try:**
- "Who is the top sales rep by total revenue?"
- "Show a bar chart of revenue by product category"
- "What is the monthly revenue trend? Show as a line chart"
- "Which region has the highest average order value?"
- "Analyze discount impact on revenue and give me a full summary"

---

### 2. `employee_hr.csv` (30 rows)
HR snapshot of a tech company: salaries, performance, attrition risk.

**Columns:** `employee_id`, `name`, `department`, `job_title`, `hire_date`, `salary`, `bonus_pct`, `years_experience`, `age`, `gender`, `location`, `is_remote`, `performance_score`, `satisfaction_score`, `attrition_risk`

**Good queries to try:**
- "What is the average salary by department?"
- "Show a scatter plot of salary vs performance score"
- "Which employees are high attrition risk? What do they have in common?"
- "Compare remote vs on-site employee satisfaction scores"
- "Who are the top 5 highest paid employees?"

---

### 3. `stock_prices.csv` (40 rows)
Weekly OHLCV data for 5 major tech stocks (Jan–Feb 2025).

**Columns:** `date`, `ticker`, `open`, `high`, `low`, `close`, `volume`, `adj_close`, `market_cap_b`, `pe_ratio`, `dividend_yield_pct`, `sector`

**Good queries to try:**
- "Show me a line chart of closing prices for all 5 stocks"
- "Which stock had the highest percentage gain over this period?"
- "Compare P/E ratios across tickers as a bar chart"
- "What is the average weekly volume by ticker?"
- "Analyze NVDA's price movement and describe the trend"

---

### 4. `marketing_campaigns.csv` (20 rows)
Digital marketing performance across channels and creatives.

**Columns:** `campaign_id`, `campaign_name`, `channel`, `start_date`, `end_date`, `budget_usd`, `impressions`, `clicks`, `conversions`, `revenue_generated`, `avg_cpc`, `avg_cpm`, `ctr_pct`, `conversion_rate_pct`, `roas`, `target_audience`, `creative_format`, `ab_variant`

**Good queries to try:**
- "Which channel has the highest ROAS?"
- "Show a bar chart of budget vs revenue by channel"
- "What is the best performing creative format by conversion rate?"
- "Compare A/B test variants for the Spring Launch and Summer Preview campaigns"
- "Which campaigns are losing money (ROAS < 1)?"

---

### 5. `patient_health_records.csv` (35 rows)
Anonymized clinical data with vitals, lifestyle factors, and diagnosis outcomes.

**Columns:** `patient_id`, `age`, `gender`, `bmi`, `blood_pressure_sys`, `blood_pressure_dia`, `cholesterol_total`, `hdl_cholesterol`, `ldl_cholesterol`, `blood_glucose_fasting`, `hba1c`, `smoker`, `physical_activity_days_pw`, `alcohol_units_pw`, `sleep_hours_avg`, `stress_level`, `diagnosis`, `medication_count`, `hospital_visits_6m`, `treatment_outcome`

**Good queries to try:**
- "What is the average BMI and blood pressure by diagnosis group?"
- "Show a scatter plot of BMI vs blood pressure"
- "Do smokers have worse treatment outcomes? Analyze and explain"
- "Which lifestyle factors correlate most with a diabetes/hypertension diagnosis?"
- "Show the distribution of ages across diagnosis categories as a bar chart"

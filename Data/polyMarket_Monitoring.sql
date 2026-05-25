INSERT INTO "polyMarket_Monitoring" (
  "condition_id", "question", "Translation", "Subject", "endDate", "rules", "days_to_end", "UseRss", "url", "option_name",
  "condition_id_ext", "token", "bid", "l1_spread_c", "band_c_used", "depth_ask_1c_usd", "depth_bid_1c_usd", "depth_1c_usd",
  "depth_ask_1c_qty", "depth_bid_1c_qty", "vwap_ask_1c", "vwap_bid_1c", "n_orders_ask_1c", "n_orders_bid_1c",
  "top_concentration_ask_1c", "top_concentration_bid_1c", "roi_if_win", "daily_eff_if_win", "apr_eff_if_win",
  "query_time_beijing", "ingested_at", "News", "llm_is_clearcut", "llm_prediction_p", "llm_explain", "suggested_qty",
  "source_file", "Score", "resolutionSource", "KeyWords", "LLM_researh", "yes_token", "no_token", "now_utc", "end_utc",
  "days_to_end_calc", "Search_words", "Yes_ask", "Yes_bid", "No_ask", "No_bid", "Yes_depth_ask_1c_usd", "Yes_depth_bid_1c_usd",
  "No_depth_ask_1c_usd", "No_depth_bid_1c_usd", "Yes_now_qty", "No_now_qty", "Yes_avg_cost", "No_avg_cost",
  "initial_capital", "profit_roll_ratio", "realized_profit", "strategy_bankroll", "IsCodeOk?", "Strategy", "Code", "查询", "LLM_Think", "UseData", "CFT",
  "Inputs1", "Inputs2", "Inputs3", "Inputs4", "Inputs5", "Inputs6", "Inputs7",
  "Inputs8", "Inputs9", "Inputs10", "Inputs11", "Inputs12", "Inputs13"
)
SELECT
  "condition_id", "question", "Translation", "Subject", "endDate", "rules", "days_to_end", "UseRss", "url", "option_name",
  "condition_id_ext", "token", "bid", "l1_spread_c", "band_c_used", "depth_ask_1c_usd", "depth_bid_1c_usd", "depth_1c_usd",
  "depth_ask_1c_qty", "depth_bid_1c_qty", "vwap_ask_1c", "vwap_bid_1c", "n_orders_ask_1c", "n_orders_bid_1c",
  "top_concentration_ask_1c", "top_concentration_bid_1c", "roi_if_win", "daily_eff_if_win", "apr_eff_if_win",
  "query_time_beijing", "ingested_at", "News", "llm_is_clearcut", "llm_prediction_p", "llm_explain", "suggested_qty",
  "source_file", "Score", "resolutionSource", "KeyWords", "LLM_researh", "yes_token", "no_token", "now_utc", "end_utc",
  "days_to_end_calc", "Search_words", "Yes_ask", "Yes_bid", "No_ask", "No_bid", "Yes_depth_ask_1c_usd", "Yes_depth_bid_1c_usd",
  "No_depth_ask_1c_usd", "No_depth_bid_1c_usd", "Yes_now_qty", "No_now_qty", "Yes_avg_cost", "No_avg_cost",
  "initial_capital", "profit_roll_ratio", "realized_profit", "strategy_bankroll", "IsCodeOk?", "Strategy", "Code", "查询", "LLM_Think", "UseData", "CFT",
  "Inputs1", "Inputs2", "Inputs3", "Inputs4", "Inputs5", "Inputs6", "Inputs7",
  "Inputs8", "Inputs9", "Inputs10", "Inputs11", "Inputs12", "Inputs13"
FROM "polyMarket_Monitor";
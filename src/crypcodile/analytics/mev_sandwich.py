import polars as pl

class MEVSandwichFilter:
    @staticmethod
    def detect_sandwiches(df: pl.DataFrame) -> pl.DataFrame:
        """
        Accepts a Polars DataFrame of trades.
        Returns a DataFrame marking matching sandwich transactions with 'is_sandwich' column.
        """
        if df.height == 0:
            if "is_sandwich" not in df.columns:
                return df.with_columns(pl.lit(False).alias("is_sandwich"))
            return df

        # Ensure dataframe is sorted by block, pool, and log_index
        df = df.sort(["block", "pool", "log_index"])

        # Calculate shifted fields grouped by block and pool
        df = df.with_columns([
            pl.col("sender").shift(1).over(["block", "pool"]).alias("prev_sender"),
            pl.col("sender").shift(-1).over(["block", "pool"]).alias("next_sender"),
            pl.col("is_buy").shift(1).over(["block", "pool"]).alias("prev_is_buy"),
            pl.col("is_buy").shift(-1).over(["block", "pool"]).alias("next_is_buy"),
            
            pl.col("sender").shift(-2).over(["block", "pool"]).alias("next_next_sender"),
            pl.col("is_buy").shift(-2).over(["block", "pool"]).alias("next_next_is_buy"),
            
            pl.col("sender").shift(2).over(["block", "pool"]).alias("prev_prev_sender"),
            pl.col("is_buy").shift(2).over(["block", "pool"]).alias("prev_prev_is_buy"),
        ])
        
        # Victim trade logic
        is_victim = (
            (pl.col("prev_sender") == pl.col("next_sender")) &
            (pl.col("prev_sender") != pl.col("sender")) &
            (pl.col("prev_is_buy") == pl.col("is_buy")) &
            (pl.col("next_is_buy") != pl.col("is_buy"))
        )
        
        # Frontrun trade logic
        is_frontrun = (
            (pl.col("next_next_sender") == pl.col("sender")) &
            (pl.col("next_sender") != pl.col("sender")) &
            (pl.col("next_is_buy") == pl.col("is_buy")) &
            (pl.col("next_next_is_buy") != pl.col("is_buy"))
        )
        
        # Backrun trade logic
        is_backrun = (
            (pl.col("prev_prev_sender") == pl.col("sender")) &
            (pl.col("prev_sender") != pl.col("sender")) &
            (pl.col("prev_is_buy") != pl.col("is_buy")) &
            (pl.col("prev_prev_is_buy") != pl.col("is_buy"))
        )
        
        df = df.with_columns(
            (is_victim | is_frontrun | is_backrun).fill_null(False).alias("is_sandwich")
        )
        
        df = df.drop([
            "prev_sender", "next_sender", "prev_is_buy", "next_is_buy",
            "next_next_sender", "next_next_is_buy", "prev_prev_sender", "prev_prev_is_buy"
        ])
        
        return df

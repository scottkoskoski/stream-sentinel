# /stream-sentinel/src/data/analysis/ieee_cis_analyzer.py

"""
IEEE-CIS Fraud Detection Dataset Analyzer

This module performs comprehensive analysis of the IEEE-CIS fraud detection dataset
to understand patterns, distributions, and characteristics that will inform our
synthetic transaction data generator.

Key Analysis Areas:
1. Schema and data type analysis
2. Missing value patterns
3. Fraud vs legitimate transaction patterns
4. Temporal analysis and seasonality
5. Feature distributions and correlations
6. Categorical value analysis

The output creates a detailed specification for realistic synthetic data generation
that matches real-world fraud detection scenarios.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from typing import Dict, List, Tuple, Any, Optional
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
import warnings

warnings.filterwarnings("ignore")

# Set up plotting style
plt.style.use("default")
sns.set_palette("husl")


class IEEECISAnalyzer:
    """
    Comprehensive analyzer for the IEEE-CIS fraud detection dataset.

    This class provides detailed analysis capabilities to understand:
    - Dataset schema and structure
    - Fraud patterns and distributions
    - Feature engineering insights
    - Synthetic data generation parameters
    """

    def __init__(
        self,
        data_path: str = "data/raw/train_transaction.csv",
        identity_path: Optional[str] = "data/raw/train_identity.csv",
    ):
        """
        Initialize the analyzer with dataset paths.

        Args:
            data_path: Path to main transaction dataset
            identity_path: Path to identity dataset (optional)
        """
        self.data_path = Path(data_path)
        self.identity_path = Path(identity_path) if identity_path else None

        # Setup logging
        self.logger = self._setup_logging()

        # Data containers
        self.transaction_df = None
        self.identity_df = None
        self.analysis_results = {}

        # Feature categories (based on IEEE-CIS documentation)
        self.feature_categories = {
            "core": [
                "TransactionID",
                "isFraud",
                "TransactionDT",
                "TransactionAmt",
                "ProductCD",
            ],
            "card": [f"card{i}" for i in range(1, 7)],
            "address": ["addr1", "addr2"],
            "distance": ["dist1", "dist2"],
            "email": ["P_emaildomain", "R_emaildomain"],
            "counting": [f"C{i}" for i in range(1, 15)],
            "timedelta": [f"D{i}" for i in range(1, 16)],
            "match": [f"M{i}" for i in range(1, 10)],
            "vesta": [f"V{i}" for i in range(1, 340)],
        }

        self.logger.info("IEEE-CIS Analyzer initialized")

    def _setup_logging(self) -> logging.Logger:
        """Setup logging configuration."""
        logger = logging.getLogger("ieee_cis_analyzer")

        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)

        return logger

    def load_data(self, sample_size: Optional[int] = None) -> bool:
        """
        Load the IEEE-CIS datasets.

        Args:
            sample_size: Optional sample size for faster analysis during development

        Returns:
            bool: True if data loaded successfully
        """
        try:
            self.logger.info(f"Loading transaction data from {self.data_path}")

            # Load main transaction dataset
            if sample_size:
                self.logger.info(f"Loading sample of {sample_size} transactions")
                # Read header first to get column names
                header = pd.read_csv(self.data_path, nrows=0)
                # Read sample
                self.transaction_df = pd.read_csv(
                    self.data_path, nrows=sample_size, dtype={"TransactionID": "int64"}
                )
            else:
                self.transaction_df = pd.read_csv(
                    self.data_path, dtype={"TransactionID": "int64"}
                )

            self.logger.info(
                f"Loaded {len(self.transaction_df):,} transactions with {len(self.transaction_df.columns)} features"
            )

            # Load identity dataset if available
            if self.identity_path and self.identity_path.exists():
                self.logger.info(f"Loading identity data from {self.identity_path}")
                self.identity_df = pd.read_csv(
                    self.identity_path, dtype={"TransactionID": "int64"}
                )
                self.logger.info(
                    f"Loaded {len(self.identity_df):,} identity records with {len(self.identity_df.columns)} features"
                )

            return True

        except Exception as e:
            self.logger.error(f"Failed to load data: {e}")
            return False

    def analyze_schema(self) -> Dict[str, Any]:
        """
        Analyze dataset schema and structure.

        Returns:
            Dict containing schema analysis results
        """
        self.logger.info("Analyzing dataset schema...")

        schema_info = {
            "total_transactions": len(self.transaction_df),
            "total_features": len(self.transaction_df.columns),
            "fraud_rate": self.transaction_df["isFraud"].mean(),
            "date_range": self._analyze_date_range(),
            "feature_types": self._analyze_feature_types(),
            "missing_patterns": self._analyze_missing_patterns(),
            "categorical_features": self._analyze_categorical_features(),
        }

        self.analysis_results["schema"] = schema_info

        # Print key findings
        self.logger.info(f"Dataset Overview:")
        self.logger.info(
            f"  - Total transactions: {schema_info['total_transactions']:,}"
        )
        self.logger.info(
            f"  - Fraud rate: {schema_info['fraud_rate']:.4f} ({schema_info['fraud_rate']*100:.2f}%)"
        )
        self.logger.info(
            f"  - Date range: {schema_info['date_range']['days']:.1f} days"
        )
        self.logger.info(
            f"  - Features with >50% missing: {len([f for f, p in schema_info['missing_patterns'].items() if p > 0.5])}"
        )

        return schema_info

    def _analyze_date_range(self) -> Dict[str, Any]:
        """Analyze the temporal range of transactions."""
        min_dt = self.transaction_df["TransactionDT"].min()
        max_dt = self.transaction_df["TransactionDT"].max()

        # Convert to approximate dates (TransactionDT is seconds from reference point)
        reference_date = datetime(
            2017, 12, 1
        )  # Approximate reference based on competition
        min_date = reference_date + timedelta(seconds=int(min_dt))
        max_date = reference_date + timedelta(seconds=int(max_dt))

        return {
            "min_dt": int(min_dt),
            "max_dt": int(max_dt),
            "range_seconds": int(max_dt - min_dt),
            "days": (max_dt - min_dt) / (24 * 3600),
            "approx_start": min_date.strftime("%Y-%m-%d"),
            "approx_end": max_date.strftime("%Y-%m-%d"),
        }

    def _analyze_feature_types(self) -> Dict[str, List[str]]:
        """Categorize features by data type."""
        feature_types = {
            "numeric_float": [],
            "numeric_int": [],
            "categorical": [],
            "boolean": [],
        }

        for col in self.transaction_df.columns:
            if col == "TransactionID":
                continue

            dtype = str(self.transaction_df[col].dtype)

            if "float" in dtype:
                feature_types["numeric_float"].append(col)
            elif "int" in dtype:
                feature_types["numeric_int"].append(col)
            elif dtype == "object":
                # Check if it's actually boolean encoded as T/F
                unique_vals = set(self.transaction_df[col].dropna().unique())
                if unique_vals.issubset({"T", "F"}):
                    feature_types["boolean"].append(col)
                else:
                    feature_types["categorical"].append(col)
            else:
                feature_types["categorical"].append(col)

        return feature_types

    def _analyze_missing_patterns(self) -> Dict[str, float]:
        """Analyze missing value patterns across features."""
        missing_percentages = {}

        for col in self.transaction_df.columns:
            if col != "TransactionID":
                missing_pct = self.transaction_df[col].isna().mean()
                if missing_pct > 0:
                    missing_percentages[col] = missing_pct

        return dict(
            sorted(missing_percentages.items(), key=lambda x: x[1], reverse=True)
        )

    def _analyze_categorical_features(self) -> Dict[str, Dict[str, Any]]:
        """Analyze categorical features and their value distributions."""
        categorical_info = {}

        # Key categorical features to analyze
        categorical_features = ["ProductCD", "P_emaildomain", "R_emaildomain"] + [
            f"M{i}" for i in range(1, 10)
        ]

        for col in categorical_features:
            if col in self.transaction_df.columns:
                value_counts = self.transaction_df[col].value_counts()

                categorical_info[col] = {
                    "unique_values": len(value_counts),
                    "most_common": value_counts.head(10).to_dict(),
                    "missing_rate": self.transaction_df[col].isna().mean(),
                }

        return categorical_info

    def analyze_fraud_patterns(self) -> Dict[str, Any]:
        """
        Analyze differences between fraudulent and legitimate transactions.

        Returns:
            Dict containing fraud pattern analysis
        """
        self.logger.info("Analyzing fraud vs legitimate transaction patterns...")

        fraud_analysis = {
            "basic_stats": self._fraud_basic_statistics(),
            "amount_patterns": self._analyze_fraud_amounts(),
            "temporal_patterns": self._analyze_fraud_temporal(),
            "feature_importance": self._analyze_fraud_feature_differences(),
        }

        self.analysis_results["fraud_patterns"] = fraud_analysis
        return fraud_analysis

    def _fraud_basic_statistics(self) -> Dict[str, Any]:
        """Basic fraud vs legitimate statistics."""
        fraud_df = self.transaction_df[self.transaction_df["isFraud"] == 1]
        legit_df = self.transaction_df[self.transaction_df["isFraud"] == 0]

        return {
            "total_fraud": len(fraud_df),
            "total_legitimate": len(legit_df),
            "fraud_rate": len(fraud_df) / len(self.transaction_df),
            "avg_fraud_amount": fraud_df["TransactionAmt"].mean(),
            "avg_legit_amount": legit_df["TransactionAmt"].mean(),
            "median_fraud_amount": fraud_df["TransactionAmt"].median(),
            "median_legit_amount": legit_df["TransactionAmt"].median(),
        }

    def _analyze_fraud_amounts(self) -> Dict[str, Any]:
        """Analyze transaction amount patterns for fraud vs legitimate."""
        fraud_amounts = self.transaction_df[self.transaction_df["isFraud"] == 1][
            "TransactionAmt"
        ]
        legit_amounts = self.transaction_df[self.transaction_df["isFraud"] == 0][
            "TransactionAmt"
        ]

        return {
            "fraud_amount_stats": {
                "mean": fraud_amounts.mean(),
                "median": fraud_amounts.median(),
                "std": fraud_amounts.std(),
                "min": fraud_amounts.min(),
                "max": fraud_amounts.max(),
                "q25": fraud_amounts.quantile(0.25),
                "q75": fraud_amounts.quantile(0.75),
            },
            "legit_amount_stats": {
                "mean": legit_amounts.mean(),
                "median": legit_amounts.median(),
                "std": legit_amounts.std(),
                "min": legit_amounts.min(),
                "max": legit_amounts.max(),
                "q25": legit_amounts.quantile(0.25),
                "q75": legit_amounts.quantile(0.75),
            },
            "amount_ranges": self._analyze_amount_ranges(),
        }

    def _analyze_amount_ranges(self) -> Dict[str, float]:
        """Analyze fraud rates across different amount ranges."""
        # Define amount bins
        bins = [0, 10, 50, 100, 500, 1000, 5000, float("inf")]
        labels = ["<$10", "$10-50", "$50-100", "$100-500", "$500-1k", "$1k-5k", ">$5k"]

        self.transaction_df["amount_range"] = pd.cut(
            self.transaction_df["TransactionAmt"],
            bins=bins,
            labels=labels,
            include_lowest=True,
        )

        fraud_by_range = self.transaction_df.groupby("amount_range")["isFraud"].agg(
            ["count", "sum", "mean"]
        )

        return {
            range_name: {
                "total_transactions": int(row["count"]),
                "fraud_transactions": int(row["sum"]),
                "fraud_rate": float(row["mean"]),
            }
            for range_name, row in fraud_by_range.iterrows()
        }

    def _analyze_fraud_temporal(self) -> Dict[str, Any]:
        """Analyze temporal patterns in fraud transactions."""
        # Convert TransactionDT to hour of day and day of week approximations
        seconds_per_hour = 3600
        seconds_per_day = 24 * seconds_per_hour

        self.transaction_df["hour_approx"] = (
            self.transaction_df["TransactionDT"] // seconds_per_hour
        ) % 24
        self.transaction_df["day_approx"] = (
            self.transaction_df["TransactionDT"] // seconds_per_day
        ) % 7

        # Analyze fraud rates by time patterns
        fraud_by_hour = self.transaction_df.groupby("hour_approx")["isFraud"].agg(
            ["count", "sum", "mean"]
        )
        fraud_by_day = self.transaction_df.groupby("day_approx")["isFraud"].agg(
            ["count", "sum", "mean"]
        )

        return {
            "hourly_patterns": {
                int(hour): {
                    "total": int(row["count"]),
                    "fraud_count": int(row["sum"]),
                    "fraud_rate": float(row["mean"]),
                }
                for hour, row in fraud_by_hour.iterrows()
            },
            "daily_patterns": {
                int(day): {
                    "total": int(row["count"]),
                    "fraud_count": int(row["sum"]),
                    "fraud_rate": float(row["mean"]),
                }
                for day, row in fraud_by_day.iterrows()
            },
        }

    def _analyze_fraud_feature_differences(self) -> Dict[str, float]:
        """Analyze key feature differences between fraud and legitimate transactions."""
        fraud_df = self.transaction_df[self.transaction_df["isFraud"] == 1]
        legit_df = self.transaction_df[self.transaction_df["isFraud"] == 0]

        feature_differences = {}

        # Analyze numeric features with sufficient data
        numeric_features = (
            ["TransactionAmt"]
            + [f"C{i}" for i in range(1, 15)]
            + [f"D{i}" for i in range(1, 16)]
        )

        for feature in numeric_features:
            if feature in self.transaction_df.columns:
                # Only analyze if both groups have sufficient non-null data
                fraud_values = fraud_df[feature].dropna()
                legit_values = legit_df[feature].dropna()

                if len(fraud_values) > 100 and len(legit_values) > 100:
                    # Calculate normalized difference in means
                    fraud_mean = fraud_values.mean()
                    legit_mean = legit_values.mean()

                    if legit_mean != 0:
                        pct_difference = abs((fraud_mean - legit_mean) / legit_mean)
                        feature_differences[feature] = pct_difference

        # Return top differing features
        return dict(
            sorted(feature_differences.items(), key=lambda x: x[1], reverse=True)[:20]
        )

    def generate_synthetic_spec(self) -> Dict[str, Any]:
        """
        Generate specifications for synthetic data generation.

        Returns:
            Dict containing parameters for synthetic transaction generation
        """
        self.logger.info("Generating synthetic data generation specifications...")

        if "schema" not in self.analysis_results:
            self.analyze_schema()

        if "fraud_patterns" not in self.analysis_results:
            self.analyze_fraud_patterns()

        spec = {
            "transaction_patterns": self._generate_transaction_patterns(),
            "fraud_patterns": self._generate_fraud_generation_rules(),
            "feature_distributions": self._generate_feature_distributions(),
            "temporal_patterns": self._generate_temporal_patterns(),
            "categorical_distributions": self._generate_categorical_distributions(),
        }

        self.analysis_results["synthetic_spec"] = spec
        return spec

    def _generate_transaction_patterns(self) -> Dict[str, Any]:
        """Generate patterns for normal transaction generation."""
        legit_df = self.transaction_df[self.transaction_df["isFraud"] == 0]

        return {
            "amount_distribution": {
                "type": "lognormal",  # Transaction amounts typically follow log-normal
                "mean_log": np.log(legit_df["TransactionAmt"]).mean(),
                "std_log": np.log(legit_df["TransactionAmt"]).std(),
                "min_amount": float(legit_df["TransactionAmt"].min()),
                "max_amount": float(
                    legit_df["TransactionAmt"].quantile(0.95)
                ),  # Cap at 95th percentile
            },
            "product_codes": legit_df["ProductCD"]
            .value_counts(normalize=True)
            .to_dict(),
            "hourly_distribution": dict(
                legit_df["hour_approx"].value_counts(normalize=True)
            ),
            "daily_distribution": dict(
                legit_df["day_approx"].value_counts(normalize=True)
            ),
        }

    def _generate_fraud_generation_rules(self) -> Dict[str, Any]:
        """Generate rules for fraudulent transaction generation."""
        fraud_df = self.transaction_df[self.transaction_df["isFraud"] == 1]

        return {
            "base_fraud_rate": float(self.analysis_results["schema"]["fraud_rate"]),
            "amount_patterns": {
                "high_amount_bias": fraud_df["TransactionAmt"].quantile(0.75)
                / self.transaction_df[self.transaction_df["isFraud"] == 0][
                    "TransactionAmt"
                ].quantile(0.75),
                "common_ranges": list(
                    self.analysis_results["fraud_patterns"]["amount_patterns"][
                        "amount_ranges"
                    ].keys()
                ),
            },
            "temporal_bias": {
                "high_risk_hours": [
                    hour
                    for hour, data in self.analysis_results["fraud_patterns"][
                        "temporal_patterns"
                    ]["hourly_patterns"].items()
                    if data["fraud_rate"]
                    > self.analysis_results["schema"]["fraud_rate"] * 1.2
                ]
            },
        }

    def _generate_feature_distributions(self) -> Dict[str, Dict[str, float]]:
        """Generate statistical distributions for key features."""
        distributions = {}

        # Analyze key numeric features
        key_features = [
            "TransactionAmt",
            "card1",
            "card2",
            "card3",
            "addr1",
            "addr2",
            "dist1",
            "dist2",
        ]

        for feature in key_features:
            if feature in self.transaction_df.columns:
                series = self.transaction_df[feature].dropna()
                if len(series) > 1000:  # Only analyze features with sufficient data
                    distributions[feature] = {
                        "mean": float(series.mean()),
                        "std": float(series.std()),
                        "min": float(series.min()),
                        "max": float(series.max()),
                        "q25": float(series.quantile(0.25)),
                        "median": float(series.median()),
                        "q75": float(series.quantile(0.75)),
                        "missing_rate": float(
                            self.transaction_df[feature].isna().mean()
                        ),
                    }

        return distributions

    def _generate_temporal_patterns(self) -> Dict[str, Any]:
        """Generate temporal patterns for realistic time-based generation."""
        return {
            "transaction_frequency": {
                "avg_transactions_per_hour": len(self.transaction_df)
                / self.analysis_results["schema"]["date_range"]["days"]
                / 24,
                "peak_hours": list(range(10, 22)),  # Typical business hours
                "low_hours": list(range(0, 6)),  # Overnight hours
            },
            "weekly_patterns": {
                "weekday_multiplier": 1.2,  # Higher volume on weekdays
                "weekend_multiplier": 0.8,  # Lower volume on weekends
            },
        }

    def _generate_categorical_distributions(self) -> Dict[str, Dict[str, float]]:
        """Generate categorical feature distributions."""
        categorical_distributions = {}

        key_categoricals = ["ProductCD", "P_emaildomain", "R_emaildomain"]

        for feature in key_categoricals:
            if feature in self.transaction_df.columns:
                # Get value counts and normalize
                value_counts = self.transaction_df[feature].value_counts(
                    normalize=True, dropna=False
                )
                categorical_distributions[feature] = value_counts.to_dict()

        return categorical_distributions

    def save_analysis_results(
        self, output_path: str = "data/processed/ieee_cis_analysis.json"
    ) -> bool:
        """
        Save analysis results to JSON file.

        Args:
            output_path: Path to save the analysis results

        Returns:
            bool: True if saved successfully
        """
        try:
            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)

            # Convert numpy types to Python types for JSON serialization
            def convert_numpy_types(obj):
                if isinstance(obj, np.integer):
                    return int(obj)
                elif isinstance(obj, np.floating):
                    return float(obj)
                elif isinstance(obj, np.ndarray):
                    return obj.tolist()
                elif isinstance(obj, dict):
                    return {k: convert_numpy_types(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [convert_numpy_types(item) for item in obj]
                return obj

            # Add metadata
            results_with_metadata = {
                "analysis_metadata": {
                    "analysis_date": datetime.now().isoformat(),
                    "dataset_path": str(self.data_path),
                    "total_transactions_analyzed": len(self.transaction_df),
                    "analyzer_version": "1.0.0",
                },
                "analysis_results": convert_numpy_types(self.analysis_results),
            }

            with open(output_file, "w") as f:
                json.dump(results_with_metadata, f, indent=2)

            self.logger.info(f"Analysis results saved to {output_file}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to save analysis results: {e}")
            return False

    def run_complete_analysis(
        self, sample_size: Optional[int] = None, save_results: bool = True
    ) -> Dict[str, Any]:
        """
        Run the complete analysis pipeline.

        Args:
            sample_size: Optional sample size for faster analysis
            save_results: Whether to save results to file

        Returns:
            Dict containing all analysis results
        """
        self.logger.info("=" * 60)
        self.logger.info("Starting Complete IEEE-CIS Dataset Analysis")
        self.logger.info("=" * 60)

        try:
            # Load data
            if not self.load_data(sample_size=sample_size):
                raise Exception("Failed to load dataset")

            # Run analysis phases
            self.logger.info("\nPhase 1: Schema Analysis")
            self.analyze_schema()

            self.logger.info("\nPhase 2: Fraud Pattern Analysis")
            self.analyze_fraud_patterns()

            self.logger.info("\nPhase 3: Synthetic Data Specification Generation")
            self.generate_synthetic_spec()

            # Save results if requested
            if save_results:
                self.save_analysis_results()

            self.logger.info("\n" + "=" * 60)
            self.logger.info("Analysis Complete!")
            self.logger.info("=" * 60)

            return self.analysis_results

        except Exception as e:
            self.logger.error(f"Analysis failed: {e}")
            raise


def main():
    """Main function for running the analyzer."""
    analyzer = IEEECISAnalyzer()

    # Run analysis with a sample for development (remove sample_size for full analysis)
    results = analyzer.run_complete_analysis(sample_size=50000)

    # Print key findings
    print("\n" + "=" * 50)
    print("KEY FINDINGS FOR SYNTHETIC DATA GENERATION")
    print("=" * 50)

    schema = results["schema"]
    fraud_patterns = results["fraud_patterns"]

    print(f"Dataset Overview:")
    print(
        f"  - Fraud Rate: {schema['fraud_rate']:.4f} ({schema['fraud_rate']*100:.2f}%)"
    )
    print(
        f"  - Average Fraud Amount: ${fraud_patterns['basic_stats']['avg_fraud_amount']:.2f}"
    )
    print(
        f"  - Average Legit Amount: ${fraud_patterns['basic_stats']['avg_legit_amount']:.2f}"
    )

    print(f"\nTemporal Patterns:")
    hourly = fraud_patterns["temporal_patterns"]["hourly_patterns"]
    peak_fraud_hour = max(hourly.items(), key=lambda x: x[1]["fraud_rate"])
    print(
        f"  - Peak Fraud Hour: {peak_fraud_hour[0]}:00 ({peak_fraud_hour[1]['fraud_rate']:.4f} fraud rate)"
    )

    print(f"\nAmount Range Analysis:")
    for range_name, data in fraud_patterns["amount_patterns"]["amount_ranges"].items():
        print(
            f"  - {range_name}: {data['fraud_rate']:.4f} fraud rate ({data['fraud_transactions']} fraud / {data['total_transactions']} total)"
        )


if __name__ == "__main__":
    main()

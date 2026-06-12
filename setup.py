from setuptools import setup, find_packages

with open("requirements.txt") as f:
    required = f.read().splitlines()

setup(
    name="smart-retail-replenishment",
    version="1.0.0",
    description="智慧零售补货建议命令行工具 - 批量分析门店缺货风险",
    author="Smart Retail Team",
    packages=find_packages(),
    install_requires=required,
    entry_points={
        "console_scripts": [
            "retail-replenish=smart_retail_replenishment.cli:cli",
        ],
    },
    python_requires=">=3.8",
)

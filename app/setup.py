from setuptools import setup, find_packages

setup(
    name="ntx-app",
    version="0.1.0",
    packages=find_packages(include=["ntx*", "ntxconfig*", "ntx_users*"]),
    # exclude non-Python directories
    exclude_package_data={"": ["frontend*", "static*", "templates*"]},
)

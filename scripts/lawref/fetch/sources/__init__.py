"""数据源：国家法律法规数据库（flk）、国家规章库（gov、gov_local）、维基文库（wikisource）与最高法/最高检/司法部的指导案例。"""
from .base import Provider
from .flk import FlkQuery, NationalLawDatabaseProvider
from .gov import GovRegulationDatabaseProvider
from .gov_local import GovLocalRegulationProvider
from .guide_case import GuideCaseProvider
from .jc_case import JcCaseProvider
from .wikisource import WikisourceProvider
from .xzal_case import XzfyCaseProvider

__all__ = ["FlkQuery", "GovLocalRegulationProvider", "GovRegulationDatabaseProvider",
           "GuideCaseProvider", "JcCaseProvider", "NationalLawDatabaseProvider", "Provider",
           "WikisourceProvider", "XzfyCaseProvider"]

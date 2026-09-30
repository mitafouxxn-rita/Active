"""corpnorm — 日本の法人名・住所データ正規化エンジン。

主要な公開 API:

>>> from corpnorm import normalize_name, normalize_address
>>> normalize_name("（株）ＡＢＣ").normalized
'株式会社ABC'
"""

from corpnorm.address import NormalizedAddress, normalize_address
from corpnorm.issues import Issue, IssueCode, Severity
from corpnorm.name import NormalizedName, normalize_name

__version__ = "1.0.0"

__all__ = [
    "Issue",
    "IssueCode",
    "NormalizedAddress",
    "NormalizedName",
    "Severity",
    "__version__",
    "normalize_address",
    "normalize_name",
]

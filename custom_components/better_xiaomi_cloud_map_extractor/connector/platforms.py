"""Which vendor class downloads and parses the map for each map format (`VacuumApi`)."""
from typing import Type

from .vacuums.base.model import VacuumApi
from .vacuums.base.vacuum_base import BaseXiaomiCloudVacuum
from .vacuums.vacuum_dreame import DreameCloudVacuum
from .vacuums.vacuum_roborock import RoborockCloudVacuum
from .vacuums.vacuum_roidmi import RoidmiCloudVacuum
from .vacuums.vacuum_unsupported import UnsupportedCloudVacuum
from .vacuums.vacuum_viomi import ViomiCloudVacuum
from .vacuums.vacuum_ijai import IjaiCloudVacuum
from .vacuums.vacuum_xiaomi import XiaomiCloudVacuum
from .vacuums.vacuum_xtl import XtlCloudVacuum

AVAILABLE_VACUUM_PLATFORMS: dict[VacuumApi, Type[BaseXiaomiCloudVacuum]] = {v.vacuum_platform(): v for v in [
    RoborockCloudVacuum,
    ViomiCloudVacuum,
    RoidmiCloudVacuum,
    DreameCloudVacuum,
    IjaiCloudVacuum,
    XiaomiCloudVacuum,
    XtlCloudVacuum,
    UnsupportedCloudVacuum
]}

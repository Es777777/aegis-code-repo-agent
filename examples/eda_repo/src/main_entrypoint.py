from routing.rw_route import RWRoute
from placement.block_placer import BlockPlacer
from timing.timing_model import TimingModel
from integrations.vivado_tools import VivadoTools


class MainEntrypoint:
    def __init__(self):
        self.router = RWRoute()
        self.placer = BlockPlacer()
        self.timing = TimingModel()
        self.vivado = VivadoTools()

    def run(self):
        self.placer.place_design()
        self.router.route_design()
        self.timing.estimate_delay()
        self.vivado.invoke_if_needed()


class StandaloneEntrypoint:
    def main(self):
        return MainEntrypoint().run()

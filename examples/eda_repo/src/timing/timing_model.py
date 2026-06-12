class TimingGraph:
    def build_graph(self):
        return "timing graph"


class DelayEstimatorBase:
    def estimate_delay(self):
        return "base delay estimate"


class TimingModel(DelayEstimatorBase):
    def estimate_delay(self):
        return "timing analysis delay model"

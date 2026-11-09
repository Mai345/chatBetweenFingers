class StabilityTracker:
    """记录候选的稳定时长和冷却状态，不依赖具体的识别模型。"""

    def __init__(self, threshold, duration_ms, cooldown_ms):
        self.threshold = threshold
        self.duration_ms = duration_ms
        self.cooldown_ms = cooldown_ms
        self.label = None
        self.since_ms = None
        self.cooldown_until = 0
        self.last_committed = None

    def update(self, candidate, timestamp_ms, has_hand=True):
        if (
            not has_hand
            or not candidate
            or candidate["confidence"] < self.threshold
        ):
            self.label = None
            self.since_ms = None
            if not has_hand:
                return "no_hand", 0, None
            return "rejected", 0, None
        label = candidate["label"]
        if label == self.last_committed and timestamp_ms < self.cooldown_until:
            self.label = None
            self.since_ms = None
            return "cooldown", 0, None
        if self.label != label:
            self.label = label
            self.since_ms = timestamp_ms
        stable_ms = max(0, timestamp_ms - self.since_ms)
        if stable_ms < self.duration_ms:
            return "candidate", stable_ms, None
        self.last_committed = label
        self.cooldown_until = timestamp_ms + self.cooldown_ms
        self.label = None
        self.since_ms = None
        return "committed", stable_ms, candidate

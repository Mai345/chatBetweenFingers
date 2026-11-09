class StaticRecognizer:
    """Adapt hand observations to the current single-hand classifier."""

    def __init__(self, classifier):
        self._classifier = classifier

    def predict(self, hands):
        if not hands:
            return None
        return self._classifier.predict(hands[0]["landmarks"])

import logging
import threading
import queue
from google.cloud import speech

logger = logging.getLogger(__name__)

# Maps RingCX codec names to Google STT encoding enums
CODEC_MAP = {
    1: speech.RecognitionConfig.AudioEncoding.OGG_OPUS,   # OPUS
    2: speech.RecognitionConfig.AudioEncoding.MULAW,      # PCMA (approximation — use MULAW)
    3: speech.RecognitionConfig.AudioEncoding.MULAW,      # PCMU
    4: speech.RecognitionConfig.AudioEncoding.LINEAR16,   # L16
    5: speech.RecognitionConfig.AudioEncoding.FLAC,       # FLAC
}


class SegmentTranscriber:
    """
    Manages a Google STT streaming session for a single participant segment.
    Runs in a background thread, reads audio from a queue, and calls
    on_transcript_result when STT returns results.
    """

    def __init__(self, segment_id, dialog_id, participant_type, participant_name,
                 codec, sample_rate, on_transcript_result):
        self.segment_id = segment_id
        self.dialog_id = dialog_id
        self.participant_type = participant_type
        self.participant_name = participant_name
        self.codec = codec
        self.sample_rate = sample_rate or 8000
        self.on_transcript_result = on_transcript_result

        self._audio_queue = queue.Queue()
        self._stopped = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def feed_audio(self, audio_bytes):
        """Called from the gRPC servicer thread with each audio chunk."""
        if not self._stopped:
            self._audio_queue.put(audio_bytes)

    def stop(self):
        """Signal that the segment has ended."""
        self._stopped = True
        self._audio_queue.put(None)  # Sentinel to unblock the generator

    def _audio_generator(self):
        """Yields audio chunks from the queue until stopped."""
        while True:
            chunk = self._audio_queue.get()
            if chunk is None:
                return
            yield speech.StreamingRecognizeRequest(audio_content=chunk)

    def _run(self):
        """Main transcription loop — runs in background thread."""
        try:
            client = speech.SpeechClient()

            encoding = CODEC_MAP.get(self.codec, speech.RecognitionConfig.AudioEncoding.MULAW)

            config = speech.RecognitionConfig(
                encoding=encoding,
                sample_rate_hertz=self.sample_rate,
                language_code='en-AU',
                model='phone_call',
                enable_automatic_punctuation=True,
            )

            streaming_config = speech.StreamingRecognitionConfig(
                config=config,
                interim_results=True,
            )

            responses = client.streaming_recognize(
                config=streaming_config,
                requests=self._audio_generator(),
            )

            for response in responses:
                for result in response.results:
                    if result.alternatives:
                        transcript = result.alternatives[0].transcript
                        is_final = result.is_final
                        self.on_transcript_result(
                            dialog_id=self.dialog_id,
                            segment_id=self.segment_id,
                            participant_type=self.participant_type,
                            participant_name=self.participant_name,
                            text=transcript,
                            is_final=is_final,
                        )

        except Exception as e:
            logger.error(f'STT error for segment {self.segment_id}: {e}')

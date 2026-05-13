import logging
import os
import requests
from generated import streaming_pb2, streaming_pb2_grpc
from google.protobuf import empty_pb2
from transcription import SegmentTranscriber

logger = logging.getLogger(__name__)

RCAU_WEBHOOK_URL = os.environ.get('RCAU_WEBHOOK_URL', '')
RCAU_WEBHOOK_SECRET = os.environ.get('RCAU_WEBHOOK_SECRET', '')

# Participant type int → readable string (matches proto enum values)
PARTICIPANT_TYPE_NAMES = {
    0: 'UNKNOWN',
    1: 'CONTACT',
    2: 'AGENT',
    5: 'BOT',
}


def post_transcript_event(dialog_id, segment_id, participant_type, participant_name,
                          text, is_final):
    """
    POSTs a transcript line to the RCAU Flask app webhook endpoint.
    Fire-and-forget — errors are logged but don't affect the stream.
    """
    if not RCAU_WEBHOOK_URL:
        logger.warning('RCAU_WEBHOOK_URL not set — transcript not forwarded.')
        return

    payload = {
        'dialog_id': dialog_id,
        'segment_id': segment_id,
        'participant_type': participant_type,
        'participant_name': participant_name,
        'text': text,
        'is_final': is_final,
    }
    headers = {'X-Webhook-Secret': RCAU_WEBHOOK_SECRET}

    try:
        requests.post(
            f'{RCAU_WEBHOOK_URL}/api/audio_streaming/transcript-event',
            json=payload,
            headers=headers,
            timeout=5,
        )
    except Exception as e:
        logger.error(f'Failed to POST transcript event: {e}')


class StreamingServicer(streaming_pb2_grpc.StreamingServicer):
    """
    Implements the RingCX gRPC Streaming service.
    One instance of Stream() is called per conference.
    """

    def Stream(self, request_iterator, context):
        logger.info('New gRPC stream opened.')

        # Track active segment transcribers keyed by segment_id
        transcribers = {}
        dialog_id = None

        try:
            for event in request_iterator:
                event_type = event.WhichOneof('event')

                if event_type == 'dialog_init':
                    dialog = event.dialog_init.dialog
                    dialog_id = dialog.id
                    logger.info(
                        f'DialogInit: dialog_id={dialog_id} '
                        f'type={dialog.type} '
                        f'ani={dialog.ani} dnis={dialog.dnis}'
                    )

                elif event_type == 'segment_start':
                    seg = event.segment_start
                    segment_id = seg.segment_id
                    participant = seg.participant
                    audio_fmt = seg.audio_format if seg.HasField('audio_format') else None

                    codec = audio_fmt.codec if audio_fmt else 3  # Default PCMU
                    sample_rate = audio_fmt.rate if audio_fmt else 8000

                    participant_type_str = PARTICIPANT_TYPE_NAMES.get(
                        participant.type, 'UNKNOWN'
                    )
                    participant_name = participant.name if participant.name else participant_type_str

                    logger.info(
                        f'SegmentStart: segment_id={segment_id} '
                        f'participant={participant_type_str} ({participant_name})'
                    )

                    transcriber = SegmentTranscriber(
                        segment_id=segment_id,
                        dialog_id=dialog_id,
                        participant_type=participant_type_str,
                        participant_name=participant_name,
                        codec=codec,
                        sample_rate=sample_rate,
                        on_transcript_result=post_transcript_event,
                    )
                    transcribers[segment_id] = transcriber

                elif event_type == 'segment_media':
                    seg_media = event.segment_media
                    segment_id = seg_media.segment_id
                    audio_bytes = seg_media.audio_content.payload

                    transcriber = transcribers.get(segment_id)
                    if transcriber:
                        transcriber.feed_audio(audio_bytes)

                elif event_type == 'segment_stop':
                    segment_id = event.segment_stop.segment_id
                    logger.info(f'SegmentStop: segment_id={segment_id}')

                    transcriber = transcribers.pop(segment_id, None)
                    if transcriber:
                        transcriber.stop()

                elif event_type == 'segment_info':
                    # Currently unused per the proto spec
                    pass

        except Exception as e:
            logger.error(f'Error in Stream for dialog {dialog_id}: {e}')

        finally:
            # Clean up any still-running transcribers if stream closes unexpectedly
            for transcriber in transcribers.values():
                transcriber.stop()
            logger.info(f'gRPC stream closed for dialog {dialog_id}.')

        return empty_pb2.Empty()

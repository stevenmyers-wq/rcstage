import os
import logging
from concurrent import futures
import grpc
from generated import streaming_pb2_grpc
from servicer import StreamingServicer

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s — %(message)s'
)
logger = logging.getLogger(__name__)


def serve():
    port = os.environ.get('PORT', '8080')

    # Thread pool — one thread per active gRPC stream (i.e. per conference)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=50))

    streaming_pb2_grpc.add_StreamingServicer_to_server(StreamingServicer(), server)

    # Cloud Run terminates TLS — we listen on plain HTTP/2 (h2c) internally
    server.add_insecure_port(f'[::]:{port}')
    server.start()

    logger.info(f'gRPC streaming server started on port {port}')

    server.wait_for_termination()


if __name__ == '__main__':
    serve()

import argparse
import sys
sys.path.append("..")
from server import Server
import lib_cli as CLI
from protocol import *

IP_ADDRESS = "10.1.1.10"
bDoubled = False
def program_arguments():
    parser = argparse.ArgumentParser(
        description="This is a program that accepts IP address and Port number"
    )
    parser.add_argument(
        "-ip",
        "--IPv4Address",
        type=str,
        default=IP_ADDRESS,
        help="An IPv4 address in the format xxx.xxx.xxx.xxx",
    )
    parser.add_argument("-p", "--Port", type=int, default=5000, help="A port number")
    return parser.parse_args()


def custom_logic(obj: Server, client: Node, message: Protocol or str):
    global bDoubled
    CLI.message_ok("CUSTOM LOGIC", colr="BlueViolet")

    if message == ProtocolMethod.DEMO and not bDoubled:
        # Show custom logic is being ran
        CLI.message_ok("DEMO", colr="BlueViolet")

        # Send a number to the client
        message.content = "10"
        obj.send(client, message)
        
        bDoubled = True

    return False



args = program_arguments()

server = Server(host=args.IPv4Address, 
                port=args.Port, 
                custom_logic=custom_logic)


server.run()
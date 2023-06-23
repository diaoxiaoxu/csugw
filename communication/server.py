#!/bin/python3

import json
import threading
import argparse
import socket
import time
import traceback
import jsonschema
from protocol import (
    Protocols,
    Protocol,
    ProtocolState,
    ProtocolMethod,
    ProtocolType,
    Field,
)
from node import Node, HelpMenu
import lib_cli as CLI
import select
import sys

LOG_MESSAGE_SIZE = 75.0
LOG_PADDING = 0
LOG = True


# TODO: parse config file
class Server:
    def __init__(
        self, host="127.0.0.1", port=5000, custom_logic=None, custom_commands=None
    ):
        self.host = host
        self.port = port
        self.__clients = []
        self.__threads = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind((self.host, self.port))
        self.sock.listen()
        self.running = True

        self.custom_commands = [] if custom_commands is None else custom_commands
        self.custom_logic = custom_logic

        self.__exit_event = threading.Event()
        self.__locks = {
            "clients": threading.Lock(),
            "print": threading.Lock(),
            "thread": threading.Lock(),
        }

    def __process_message(self, client, message: Protocol, is_receiving=False):
        if not message:
            CLI.message_caution("GOT EMPTY MESSAGE", print_func=self.__print_thread)
            return False

        if message == Protocols.DISCONNECT:
            self.disconnect_client(client)
            return True

        if message == Protocols.SHOW:
            self.show_clients()
            return False

        if self.custom_logic is not None:
            return self.custom_logic(self, client, message)

        if not is_receiving:
            if message[ProtocolType] == ProtocolType.BROADCAST:
                self.broadcast(f"{message}", exclude=client)
            else:
                self.__send_data(message)

        return False

    def __command_line(self):
        from cli_commands import CLI_DEFAULT_COMMANDS, CLI_SERVER_COMMANDS

        self.custom_commands = self.custom_commands + CLI_SERVER_COMMANDS

        while not self.__exit_event.is_set():
            try:
                if self.__is_active(sys.stdin):
                    message = input()
                    result = self.__commands(message)
                    if result is False:
                        continue
                    if result == "VOID":
                        print("Invalid METHOD:\t", f'"{message}"\n')
                        continue
                    if Protocol.has_key(message, ProtocolMethod):
                        message = Protocol(method=message)
                        # self.broadcast(message)
                        for client in self.__clients:
                            self.__process_message(client, message, is_receiving=False)
                        # if self.__process_message(message, is_receiving=False):
                        # break
            except KeyboardInterrupt:
                break
            except ValueError:
                break
            except Exception as err:
                CLI.message_error("COMMAND LINE ERROR", print_func=self.__print_thread)
                self.__print_thread(err)
                self.__print_thread(traceback.format_exc())
                self.sock.close()
                self.__exit_event.set()
                self.running = False
                break

    def __commands(self, user_input: str):
        from cli_commands import CLI_DEFAULT_COMMANDS

        input_segments = user_input.split(" ")
        for command in self.custom_commands + CLI_DEFAULT_COMMANDS:
            if input_segments[0] in command["Commands"]:
                function = command["Function"]
                if hasattr(function, "__name__"):
                    need_self = function.__name__ in dir(self.__class__)
                else:
                    if function in dir(self.__class__):
                        need_self = True
                        function = getattr(self.__class__, function)
                    else:
                        need_self = False
                if command["Parameters"] > 0:
                    return (
                        function(self, *input_segments[1:])
                        if need_self
                        else function(*input_segments[1:])
                    )
                else:
                    return function(self) if need_self else function()
        else:
            # print(f"Command '{input_segments}' not found.")
            return True
        return "VOID"

    def __handle_client(self, client: Node):
        while not self.__exit_event.is_set():
            try:
                ready_to_read, _, _ = select.select([client.socket], [], [], 1)
                if ready_to_read:
                    message = self.__receive_data(client.socket)
                    if self.__process_message(client, message, is_receiving=True):
                        break
            except Exception as e:
                # TODO: Exception thrown on DC
                # self.print_caution("Exception in handle_client")
                # self.print_thread(e)
                self.disconnect_client(client)
                break

    def __receive(self):
        self.sock.setblocking(False)
        while not self.__exit_event.is_set():
            ready_to_read, _, _ = select.select([self.sock], [], [], 1)
            if ready_to_read:
                client, address = self.sock.accept()
                with self.__locks["clients"]:
                    if address not in self.__clients:
                        client_node = self.__initialize_client(client)
                if client_node:
                    with self.__locks["thread"]:
                        self.__threads.append(
                            threading.Thread(
                                target=self.__handle_client, args=(client_node,)
                            )
                        )
                        self.__threads[-1].start()

    def __initialize_client(self, client):
        self.__send_data(client, Protocols.INITIALIZE)
        response = self.__receive_data(client)
        if response == Protocols.DISCONNECT:
            client.shutdown(0)
            client.close()
            return

        # TODO: Implement AWK
        self.__send_data(client, Protocols.INITIALIZE)
        client_node = Node(client, response["ID"])
        self.__clients.append(client_node)
        CLI.line()
        CLI.message_ok(
            f"CLIENT CONNECTED: {str(client_node._strIPPORT)}",
            print_func=self.__print_thread,
        )

        return client_node

    def send(self, client, message: Protocol):
        self.__send_data(client, message)

    def __send_data(self, client, message: Protocol, encoding="ascii"):
        addr = (
            CLI.color("aquamarine", client._strIPPORT)
            if type(client) is Node
            else self.__get_socket_address(client)
        )
        self.__log_send(message, addr)
        message = message.to_network(
            encoding=encoding, node=client if type(client) is Node else None
        )

        if type(client) is Node:
            client.socket.sendall(message)
        else:
            client.sendall(message)

    def __receive_data(self, client, buff_size=1024, decoding="ascii") -> Protocol:
        message = client.recv(buff_size).decode(decoding)
        message: Protocol = Protocol.from_network(message)
        addr = (
            client._strIPPORT
            if type(client) is Node
            else self.__get_socket_address(client)
        )
        self.__log_receive(message, addr)
        return message

    def disconnect_client(self, client: Node):
        try:
            self.__send_data(client.socket, Protocols.DISCONNECT)
            client.close()
        except:
            pass

        with self.__locks["clients"]:
            self.__clients.remove(client)

        CLI.message_caution(
            f"CLIENT DISCONNECTED: {str(client._strIPPORT)}",
            print_func=self.__print_thread,
        )

    def broadcast(self, message, exclude=None):
        if exclude and exclude in self.__clients and len(self.__clients) == 1:
            return
        if message != Protocols.INITIALIZE:
            with self.__locks["clients"]:
                for client in self.__clients:
                    if exclude != client:
                        self.__send_data(client, message)

    def shutdown(self):
        message = Protocols.DISCONNECT
        message[ProtocolType] = ProtocolType.BROADCAST
        message[Field.ID] = "Server"

        CLI.message_caution("STOPPING SERVER...", print_func=self.__print_thread)
        self.broadcast(message)
        self.running = False
        self.__exit_event.set()
        time.sleep(1)
        with self.__locks["clients"]:
            for client in self.__clients:
                client.close()

        with self.__locks["thread"]:
            for thread in self.__threads:
                if thread.is_alive() and thread != threading.current_thread():
                    thread.join()

        self.sock.close()
        CLI.message_error("SERVER SHUTDOWN", print_func=self.__print_thread)

    def show_clients(self):
        if len(self.__clients) == 0:
            CLI.message_error("NO CLIENTS CONNECTED", print_func=self.__print_thread)
            return

        client_info = []
        for node in self.__clients:
            client_info.append(node.get_data())
        data = [list(client_info[0].keys())] + [
            list(entry.values()) for entry in client_info
        ]
        self.__print_("Connected Clients")
        CLI.table(data, showindex=True)

    def show_client(self, client: int or str):
        if type(client) is str:
            for node in self.__clients:
                if node.ID == client:
                    node.show()

        if str(client).isdigit():
            client = int(client)
            if client >= 0 and client < len(self.__clients):
                self.__clients[client].show()
            else:
                CLI.message_error(
                    "INVALID CLIENT INDEX", print_func=self.__print_thread
                )

    def run(self):
        CLI.clear_terminal()
        CLI.message_ok(
            f"SERVER STARTED {self.host}:{self.port}", print_func=self.__print_thread
        )
        self.sock.listen()
        self.__threads.extend(
            [
                threading.Thread(target=self.__receive),
                threading.Thread(target=self.__command_line),
            ]
        )
        
        [thread.start() for thread in self.__threads]
        for thread in self.__threads:
            try:
                thread.join()
            except KeyboardInterrupt:
                break
            except Exception as e:
                CLI.message_error("SERVER ERROR", print_func=self.__print_thread)
                print(e)

    def show_help_menu(self):
        from cli_commands import CLI_DEFAULT_COMMANDS

        results = CLI.create_help_menu(
            self.custom_commands, CLI_DEFAULT_COMMANDS, verbose=False
        )
        self.__print_thread(results[0])
        self.__print_thread(results[1])

    def __log_send(self, message, addr):
        if LOG:
            output = f"[LOG] {CLI.color('steelblue', 'SENDING:')}\n"
            output += f'{str(message):<{LOG_PADDING}} {"-->":<{LOG_PADDING}} {addr}\n'
            self.__print_thread(output, clr="gray")

    def __log_receive(self, message, addr):
        if LOG:
            output = f"[LOG] {CLI.color('tomato', 'RECEIVED:')}\n"
            output += f'{str(message):<{LOG_PADDING}} {"<--":<{LOG_PADDING}} {addr}\n'
            self.__print_thread(output, clr="gray")

    def __get_socket_address(self, socket_obj: socket.socket) -> str:
        peer_name = socket_obj.getpeername()
        return CLI.color("aquamarine", f"{str(peer_name[0])} : {str(peer_name[1])}")

    def __print_thread(self, *args, **kwargs):
        kwargs = {**{"sep": " ", "end": "\n"}, **kwargs}
        msg = "".join(str(arg) + kwargs["sep"] for arg in args)
        msg = CLI.color(kwargs["clr"], msg) if "clr" in kwargs else msg
        kwargs.pop("clr", "")
        with self.__locks["print"]:
            print(msg, **kwargs)

    def __print_(self, message):
        self.__print_thread(
            CLI.message(message, "lime", verbose=False, width_fraction=LOG_MESSAGE_SIZE)
        )

    def __is_active(self, stream, timeout=1):
        ready, _, _ = select.select([stream], [], [], timeout)
        return ready


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="This is a program that accepts IP address and Port number"
    )
    parser.add_argument(
        "-ip",
        "--IPv4Address",
        type=str,
        default="127.0.0.1",
        help="An IPv4 address in the format xxx.xxx.xxx.xxx",
    )
    parser.add_argument("-p", "--Port", type=int, default=5000, help="A port number")
    args = parser.parse_args()

    server = Server(host=args.IPv4Address, port=args.Port)
    server.run()

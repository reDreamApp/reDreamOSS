import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/server/server_config.dart';
import 'package:redream_app/features/server/server_address_screen.dart';

/// No server yet → the address screen. Otherwise [child] (the auth gate).
class ServerGate extends StatelessWidget {
  const ServerGate({required this.child, super.key});

  final Widget child;

  @override
  Widget build(BuildContext context) {
    final connected = context.select<ServerConfig, bool>((s) => s.isConnected);
    return connected ? child : const ServerAddressScreen();
  }
}

/// Signs out on the current server, then forgets it, so the address screen
/// shows. Read both before the first await: the caller's screen goes away.
/// The logout request is already on its way when the address is forgotten,
/// so a server that is down does not hold the change up.
Future<void> changeServer(BuildContext context) async {
  final auth = context.read<AuthService>();
  final server = context.read<ServerConfig>();
  final signedOut = auth.signOut();
  await server.disconnect();
  await signedOut;
}

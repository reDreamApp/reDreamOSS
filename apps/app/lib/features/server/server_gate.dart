import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'package:redream_app/core/auth/auth_service.dart';
import 'package:redream_app/core/server/server_config.dart';
import 'package:redream_app/features/server/server_address_screen.dart';

/// No server yet → the address screen. Otherwise [child] (the auth gate).
/// Coming back to the app asks the server about itself again, so features
/// it turned on or off show without a restart.
class ServerGate extends StatefulWidget {
  const ServerGate({required this.child, super.key});

  final Widget child;

  @override
  State<ServerGate> createState() => _ServerGateState();
}

class _ServerGateState extends State<ServerGate> {
  late final AppLifecycleListener _lifecycle;

  @override
  void initState() {
    super.initState();
    _lifecycle = AppLifecycleListener(
      onResume: () => unawaited(context.read<ServerConfig>().refresh()),
    );
  }

  @override
  void dispose() {
    _lifecycle.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final connected = context.select<ServerConfig, bool>((s) => s.isConnected);
    return connected ? widget.child : const ServerAddressScreen();
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

import 'package:redream_app/core/auth/auth_service.dart';

class FakeAuth extends AuthService {
  FakeAuth({this.userId, this.email});

  @override
  String? userId;
  @override
  String? email;

  /// Thrown by the next sign-in, sign-up or account change when set.
  Exception? error;

  final signIns = <String>[];
  final signUps = <({String email, String? inviteCode})>[];
  final passwordChanges = <({String current, String next})>[];
  final emailChanges = <({String current, String email})>[];

  @override
  String? get accessToken => userId == null ? null : 'token-$userId';

  @override
  Future<void> signIn({required String email, required String password}) async {
    _throwIfFailing();
    signIns.add(email);
    userId = 'user-1';
    this.email = email;
    notifyListeners();
  }

  @override
  Future<void> signUp({
    required String email,
    required String password,
    String? inviteCode,
  }) async {
    _throwIfFailing();
    signUps.add((email: email, inviteCode: inviteCode));
    userId = 'user-new';
    this.email = email;
    notifyListeners();
  }

  /// Runs when signOut is called, before anything changes.
  void Function()? onSignOut;

  @override
  Future<void> signOut() async {
    onSignOut?.call();
    userId = null;
    email = null;
    notifyListeners();
  }

  @override
  Future<bool> onUnauthorized(String? usedToken) async => false;

  @override
  Future<void> updateEmail({
    required String currentPassword,
    required String newEmail,
  }) async {
    _throwIfFailing();
    emailChanges.add((current: currentPassword, email: newEmail));
    email = newEmail;
    notifyListeners();
  }

  @override
  Future<void> updatePassword({
    required String currentPassword,
    required String newPassword,
  }) async {
    _throwIfFailing();
    passwordChanges.add((current: currentPassword, next: newPassword));
  }

  void _throwIfFailing() {
    if (error case final error?) throw error;
  }
}

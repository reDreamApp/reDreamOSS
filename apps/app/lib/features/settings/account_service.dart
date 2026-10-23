import 'package:redream_app/core/api/api_client.dart';

/// Account-level endpoints: export, deletion, help center.
class AccountService {
  AccountService(this._api);

  final ApiClient _api;

  Future<Map<String, dynamic>> exportData() => _api.get('/api/v1/exports/data');

  Future<void> deleteData() => _api.delete('/api/v1/users/me/data');

  Future<void> deleteAccount() => _api.delete('/api/v1/users/me');

  Future<void> submitConcern(String message, {String? email}) =>
      _api.post('/api/v1/help-center/concerns', {
        'message': message.trim(),
        'email': ?email,
      });
}

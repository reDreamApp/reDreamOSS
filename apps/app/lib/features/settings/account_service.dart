import 'package:redream_app/core/api/api_client.dart';

/// Account-level endpoints: export and deletion.
class AccountService {
  AccountService(this._api);

  final ApiClient _api;

  Future<Map<String, dynamic>> exportData() => _api.get('/api/v1/exports/data');

  Future<void> deleteAccount() => _api.delete('/api/v1/users/me');
}
